"""Finance: the school's money system of record.

Every lira that enters or leaves the business is represented here — an invoice
states what is owed, a payment states what actually moved, an expense states
what was spent, a commission record states what is owed to an instructor, and a
package is money taken today for lessons delivered later.

Two rules hold everywhere in this module:

* **Decimal only.** Amounts are ``money_field()`` (12,2). No float ever touches
  a monetary value, and every derived amount is quantised with
  ``ROUND_HALF_UP`` before it is stored.
* **Nothing is ever rewritten.** A refund is a new, negative payment row that
  points at the payment it reverses; the original stays exactly as it was
  recorded. History is what an auditor reads, so it stays immutable.
"""
