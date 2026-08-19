"""Bookings — the operational heart of the school.

Everything a customer buys that occupies a *seat in time* becomes a
:class:`~apps.bookings.models.Booking`: a lesson place, a surf-camp place, a
rental slot or a pre-paid package. The module owns seat availability, the
conflict rules that stop a school double-booking a student or breaching an
instructor ratio, the cancellation policy, and the waiting list.
"""
