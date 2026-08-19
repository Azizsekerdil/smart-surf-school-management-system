"""Customer business rules.

Everything that changes a customer record — creation, merging, roll-up
recalculation, consent changes, deactivation — goes through this module so the
audit trail is complete and the rules are testable without a request.
"""

from __future__ import annotations

import logging
from decimal import Decimal

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import transaction
from django.utils import timezone
from django.utils.translation import gettext as _

from apps.audit.models import AuditAction
from apps.audit.services import diff_instances, record_audit
from apps.core.models import Document, Note

from .models import Customer, CustomerTag, normalise_phone
from .selectors import booking_stats, customers_matching_contact, paid_total

logger = logging.getLogger("apps.customers")

ZERO = Decimal("0.00")

#: Fields copied from the duplicate onto the survivor when the survivor's own
#: value is empty. Never overwrite a value the staff explicitly maintained.
MERGE_FILLABLE_FIELDS = (
    "email",
    "phone",
    "birth_date",
    "gender",
    "nationality",
    "photo",
    "emergency_contact_name",
    "emergency_contact_phone",
    "emergency_contact_relation",
    "address_line1",
    "address_line2",
    "city",
    "state",
    "postal_code",
    "country",
)


# ---------------------------------------------------------------------------
# Create / update
# ---------------------------------------------------------------------------
@transaction.atomic
def create_customer(
    *,
    first_name: str,
    last_name: str,
    actor=None,
    request=None,
    tags=None,
    allow_duplicate: bool = False,
    **fields,
) -> Customer:
    """Create and audit a customer.

    Raises :class:`~django.core.exceptions.ValidationError` when a customer with
    the same e-mail or phone already exists, unless *allow_duplicate* is set —
    the reception desk must make that call consciously, because a duplicate
    splits somebody's history, waiver and lifetime value in two.
    """
    email = (fields.get("email") or "").strip().lower()
    phone = normalise_phone(fields.get("phone"))

    if not allow_duplicate and (email or phone):
        existing = customers_matching_contact(email=email, phone=phone).first()
        if existing is not None:
            raise ValidationError(
                {
                    "email"
                    if email and existing.email == email
                    else "phone": _(
                        "%(name)s (%(code)s) already uses this e-mail or phone number."
                    )
                    % {"name": existing.full_name, "code": existing.customer_code}
                }
            )

    customer = Customer(first_name=first_name, last_name=last_name, **fields)
    if actor is not None and getattr(actor, "is_authenticated", False):
        customer.created_by = actor
        customer.updated_by = actor
    # ``photo`` is validated by the form/serializer that accepted the upload;
    # re-reading a stored file here would only cost I/O.
    customer.full_clean(exclude=["photo", "customer_code"])
    customer.save()

    if tags:
        set_tags(customer, tags, actor=actor)

    record_audit(
        request,
        action=AuditAction.CREATE,
        instance=customer,
        user=actor,
        description=_("Customer %(code)s created") % {"code": customer.customer_code},
    )
    return customer


@transaction.atomic
def update_customer(customer: Customer, *, actor=None, request=None, tags=None, **fields) -> Customer:
    """Apply *fields* to *customer*, validate, save and record the diff."""
    before = Customer.all_objects.get(pk=customer.pk)
    for name, value in fields.items():
        setattr(customer, name, value)
    if actor is not None and getattr(actor, "is_authenticated", False):
        customer.updated_by = actor
    customer.full_clean(exclude=["photo", "customer_code"])
    customer.save()

    if tags is not None:
        set_tags(customer, tags, actor=actor)

    changes = diff_instances(before, customer)
    if changes:
        record_audit(
            request,
            action=AuditAction.UPDATE,
            instance=customer,
            user=actor,
            changes=changes,
            description=_("Customer %(code)s updated") % {"code": customer.customer_code},
        )
    return customer


def set_tags(customer: Customer, tags, *, actor=None) -> None:
    """Replace the customer's tag set, recording who applied each new tag."""
    wanted = {tag.pk if hasattr(tag, "pk") else int(tag) for tag in tags}
    current = set(
        CustomerTag.objects.filter(customer=customer).values_list("tag_id", flat=True)
    )
    CustomerTag.objects.filter(customer=customer, tag_id__in=current - wanted).delete()
    added_by = actor if (actor is not None and getattr(actor, "is_authenticated", False)) else None
    CustomerTag.objects.bulk_create(
        [
            CustomerTag(customer=customer, tag_id=tag_id, added_by=added_by)
            for tag_id in wanted - current
        ]
    )


@transaction.atomic
def set_marketing_consent(customer: Customer, granted: bool, *, actor=None, request=None) -> Customer:
    """Record an opt-in or opt-out with its timestamp — this is a legal record."""
    if customer.marketing_consent == granted:
        return customer
    customer.marketing_consent = granted
    customer.marketing_consent_at = timezone.now() if granted else None
    if actor is not None and getattr(actor, "is_authenticated", False):
        customer.updated_by = actor
    customer.save(update_fields=["marketing_consent", "marketing_consent_at", "updated_by", "updated_at"])
    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=customer,
        user=actor,
        changes={"marketing_consent": [not granted, granted]},
        description=(
            _("Marketing consent granted by %(name)s")
            if granted
            else _("Marketing consent withdrawn by %(name)s")
        )
        % {"name": customer.full_name},
    )
    return customer


@transaction.atomic
def deactivate_customer(customer: Customer, *, reason: str = "", actor=None, request=None) -> Customer:
    """Archive a customer without destroying their history."""
    stats = booking_stats(customer)
    if stats["active"]:
        raise ValidationError(
            _("This customer still has %(count)s open booking(s). Close them first.")
            % {"count": stats["active"]}
        )
    customer.is_active = False
    if actor is not None and getattr(actor, "is_authenticated", False):
        customer.updated_by = actor
    customer.save(update_fields=["is_active", "updated_by", "updated_at"])
    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=customer,
        user=actor,
        changes={"is_active": [True, False]},
        description=_("Customer %(code)s deactivated. %(reason)s")
        % {"code": customer.customer_code, "reason": reason},
    )
    return customer


@transaction.atomic
def reactivate_customer(customer: Customer, *, actor=None, request=None) -> Customer:
    customer.is_active = True
    if actor is not None and getattr(actor, "is_authenticated", False):
        customer.updated_by = actor
    customer.save(update_fields=["is_active", "updated_by", "updated_at"])
    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=customer,
        user=actor,
        changes={"is_active": [False, True]},
        description=_("Customer %(code)s reactivated") % {"code": customer.customer_code},
    )
    return customer


# ---------------------------------------------------------------------------
# Roll-ups
# ---------------------------------------------------------------------------
def recalculate_lifetime_value(customer: Customer, *, save: bool = True) -> Customer:
    """Refresh money and visit roll-ups from the modules that own the truth.

    Safe to call when the finance / bookings modules hold no rows for this
    customer: the roll-ups simply fall back to zero and ``None``.
    """
    total_paid = paid_total(customer)
    stats = booking_stats(customer)

    customer.lifetime_value = total_paid
    customer.total_bookings = stats["total"]
    if stats["first_date"] is not None:
        customer.first_visit_date = (
            stats["first_date"]
            if customer.first_visit_date is None
            else min(customer.first_visit_date, stats["first_date"])
        )
    if stats["last_date"] is not None:
        customer.last_visit_date = (
            stats["last_date"]
            if customer.last_visit_date is None
            else max(customer.last_visit_date, stats["last_date"])
        )

    if save and customer.pk:
        customer.save(
            update_fields=[
                "lifetime_value",
                "total_bookings",
                "first_visit_date",
                "last_visit_date",
                "updated_at",
            ]
        )
    return customer


def register_visit(customer: Customer, on_date=None, *, amount: Decimal | None = None) -> Customer:
    """Stamp a completed visit onto the customer.

    Called by the bookings / rentals / POS modules when a customer actually
    turns up, so the CRM screens do not have to scan every module to answer
    "when did we last see this person?".
    """
    visit_date = on_date or timezone.localdate()
    if hasattr(visit_date, "date"):
        visit_date = visit_date.date()

    fields = ["last_visit_date", "updated_at"]
    if customer.first_visit_date is None or visit_date < customer.first_visit_date:
        customer.first_visit_date = visit_date
        fields.append("first_visit_date")
    if customer.last_visit_date is None or visit_date > customer.last_visit_date:
        customer.last_visit_date = visit_date

    if amount:
        customer.lifetime_value = (customer.lifetime_value or ZERO) + Decimal(amount)
        fields.append("lifetime_value")

    customer.save(update_fields=sorted(set(fields)))
    return customer


# ---------------------------------------------------------------------------
# Duplicates & merging
# ---------------------------------------------------------------------------
def find_duplicates(limit: int = 100) -> list[dict]:
    """Group customers that are probably the same person.

    Two signals, both cheap in SQL because the columns are normalised on save:

    * identical (non-empty) e-mail address;
    * identical (non-empty) phone number *and* surname — a shared family phone
      alone is not evidence, the surname makes it one.
    """
    from django.db.models import Count

    groups: list[dict] = []
    seen: set[frozenset] = set()

    email_rows = (
        Customer.objects.exclude(email="")
        .values("email")
        .annotate(n=Count("id"))
        .filter(n__gt=1)
        .order_by("email")[:limit]
    )
    for row in email_rows:
        members = list(Customer.objects.filter(email=row["email"]).order_by("created_at"))
        key = frozenset(c.pk for c in members)
        if len(members) < 2 or key in seen:
            continue
        seen.add(key)
        groups.append(
            {"reason": _("Same e-mail address"), "value": row["email"], "customers": members}
        )

    phone_rows = (
        Customer.objects.exclude(phone="")
        .values("phone", "last_name")
        .annotate(n=Count("id"))
        .filter(n__gt=1)
        .order_by("phone")[:limit]
    )
    for row in phone_rows:
        members = list(
            Customer.objects.filter(phone=row["phone"], last_name=row["last_name"]).order_by(
                "created_at"
            )
        )
        key = frozenset(c.pk for c in members)
        if len(members) < 2 or key in seen:
            continue
        seen.add(key)
        groups.append(
            {
                "reason": _("Same phone number and surname"),
                "value": f"{row['phone']} · {row['last_name']}",
                "customers": members,
            }
        )

    return groups[:limit]


@transaction.atomic
def merge_customers(primary: Customer, duplicate: Customer, *, actor=None, request=None) -> Customer:
    """Fold *duplicate* into *primary* and archive the duplicate.

    Every row that pointed at the duplicate — bookings, rentals, payments,
    documents, notes, the student profile — is re-pointed at the survivor, so no
    history and no money is lost. The duplicate is soft-deleted, never purged:
    audit entries and old invoices still reference it by id.

    Two things deliberately do **not** move: marketing consent (see below) and a
    one-to-one profile the survivor already owns — a second student profile has
    nowhere to go, so it stays with the archived record instead of being
    destroyed.
    """
    if primary.pk == duplicate.pk:
        raise ValidationError(_("A customer cannot be merged into itself."))
    if primary.is_deleted:
        raise ValidationError(_("The surviving customer must not be an archived record."))

    moved: dict[str, int] = {}

    for relation in Customer._meta.related_objects:
        if relation.many_to_many:
            continue  # tag links are handled explicitly below
        related_model = relation.related_model
        field_name = relation.field.name
        if related_model is CustomerTag:
            continue

        manager = getattr(related_model, "all_objects", None) or related_model._default_manager
        queryset = manager.filter(**{field_name: duplicate})

        if relation.one_to_one:
            # Only one row may point at the survivor. If it already has one
            # (e.g. a student profile) the duplicate's stays behind, attached to
            # the archived record, rather than being silently destroyed.
            if manager.filter(**{field_name: primary}).exists():
                continue
        count = queryset.update(**{field_name: primary})
        if count:
            moved[f"{related_model._meta.label}.{field_name}"] = count

    # --- tags: union, keeping the earliest provenance ---------------------
    primary_tags = set(
        CustomerTag.objects.filter(customer=primary).values_list("tag_id", flat=True)
    )
    duplicate_links = list(CustomerTag.objects.filter(customer=duplicate))
    for link in duplicate_links:
        if link.tag_id in primary_tags:
            link.delete()
        else:
            link.customer = primary
            link.save(update_fields=["customer"])
            primary_tags.add(link.tag_id)

    # --- generic relations: notes and documents --------------------------
    # These hang off a GenericForeignKey, so ``related_objects`` above cannot
    # see them. Moving them matters: a signed waiver attached to the duplicate
    # must follow the survivor, or the person is blocked from the water.
    content_type = ContentType.objects.get_for_model(Customer)
    for generic_model in (Note, Document):
        count = generic_model.all_objects.filter(
            content_type=content_type, object_id=duplicate.pk
        ).update(object_id=primary.pk)
        if count:
            moved[f"{generic_model._meta.label}.object_id"] = count

    # --- fill the survivor's blanks --------------------------------------
    filled: list[str] = []
    for name in MERGE_FILLABLE_FIELDS:
        if not getattr(primary, name, None) and getattr(duplicate, name, None):
            setattr(primary, name, getattr(duplicate, name))
            filled.append(name)

    if duplicate.first_visit_date and (
        primary.first_visit_date is None or duplicate.first_visit_date < primary.first_visit_date
    ):
        primary.first_visit_date = duplicate.first_visit_date
    if duplicate.last_visit_date and (
        primary.last_visit_date is None or duplicate.last_visit_date > primary.last_visit_date
    ):
        primary.last_visit_date = duplicate.last_visit_date

    # Marketing consent is deliberately NOT inherited. We cannot tell a record
    # that was never asked from one where the person opted out, and the safe
    # direction of that ambiguity is "do not contact them". Staff re-confirm.

    if duplicate.notes.strip():
        header = _("Merged from %(code)s") % {"code": duplicate.customer_code}
        primary.notes = f"{primary.notes}\n\n--- {header} ---\n{duplicate.notes}".strip()

    if actor is not None and getattr(actor, "is_authenticated", False):
        primary.updated_by = actor
    primary.save()

    recalculate_lifetime_value(primary)

    # --- archive the duplicate -------------------------------------------
    duplicate.is_active = False
    duplicate.notes = (
        f"{duplicate.notes}\n\n"
        + str(_("Merged into %(code)s.") % {"code": primary.customer_code})
    ).strip()
    duplicate.save(update_fields=["is_active", "notes", "updated_at"])
    duplicate.delete()  # soft delete

    record_audit(
        request,
        action=AuditAction.UPDATE,
        instance=primary,
        user=actor,
        changes={"merged_from": [duplicate.customer_code, primary.customer_code], **{f: [None, getattr(primary, f)] for f in filled}},
        description=_("Customer %(dup)s merged into %(primary)s. Moved rows: %(moved)s")
        % {
            "dup": duplicate.customer_code,
            "primary": primary.customer_code,
            "moved": ", ".join(f"{k}={v}" for k, v in sorted(moved.items())) or _("none"),
        },
    )
    record_audit(
        request,
        action=AuditAction.DELETE,
        instance=duplicate,
        user=actor,
        description=_("Duplicate customer %(code)s archived after merge")
        % {"code": duplicate.customer_code},
    )
    logger.info(
        "Merged customer %s into %s (%s rows moved)",
        duplicate.customer_code,
        primary.customer_code,
        sum(moved.values()),
    )
    return primary
