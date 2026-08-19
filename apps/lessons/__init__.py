"""Lesson catalogue, scheduling and attendance.

This module owns the school's teaching timetable: the ``LessonType`` catalogue
(what can be taught, to whom, for how long and at what price), the scheduled
``Lesson`` instances, and the per-student ``LessonAttendance`` roster that
drives check-in, equipment hand-out and completion.

Safety ratios, instructor availability and spot capacity are enforced in
:mod:`apps.lessons.services` — never in a view — because they are the rules a
surf school is legally accountable for.
"""
