"""Factories for training content and progress."""

from __future__ import annotations

import factory
from factory.django import DjangoModelFactory

from apps.training.models import (
    Difficulty,
    TrainingCourse,
    TrainingLesson,
    TrainingProgress,
    TrainingStep,
)


class TrainingCourseFactory(DjangoModelFactory):
    class Meta:
        model = TrainingCourse
        django_get_or_create = ("code",)

    code = factory.Sequence(lambda n: f"course-{n}")
    title_en = factory.Sequence(lambda n: f"Create your first student {n}")
    title_tr = factory.Sequence(lambda n: f"İlk öğrencinizi oluşturun {n}")
    description_en = "From the counter to a student record that is safe to put in the water."
    description_tr = "Tezgâhtan suya girmesi güvenli bir öğrenci kaydına."
    icon = "graduation-cap"
    estimated_minutes = 10
    difficulty = Difficulty.BEGINNER
    required_capability = ""
    sort_order = 10
    is_active = True


class TrainingLessonFactory(DjangoModelFactory):
    class Meta:
        model = TrainingLesson

    course = factory.SubFactory(TrainingCourseFactory)
    order = factory.Sequence(lambda n: n + 1)
    title_en = factory.Sequence(lambda n: f"Lesson {n}")
    title_tr = factory.Sequence(lambda n: f"Ders {n}")
    summary_en = "What this stage covers."
    summary_tr = "Bu aşamanın kapsamı."
    estimated_minutes = 5


class TrainingStepFactory(DjangoModelFactory):
    class Meta:
        model = TrainingStep

    lesson = factory.SubFactory(TrainingLessonFactory)
    order = factory.Sequence(lambda n: n + 1)
    title_en = factory.Sequence(lambda n: f"Step {n}")
    title_tr = factory.Sequence(lambda n: f"Adım {n}")
    body_en = "Open the screen and fill in the form."
    body_tr = "Ekranı açın ve formu doldurun."
    target_url = ""
    action_hint_en = "Sidebar → Operations → Students"
    action_hint_tr = "Kenar çubuğu → Operasyon → Öğrenciler"


class TrainingProgressFactory(DjangoModelFactory):
    class Meta:
        model = TrainingProgress

    course = factory.SubFactory(TrainingCourseFactory)
    status = TrainingProgress.Status.NOT_STARTED
    completed_steps = factory.List([])


def build_course(lessons: int = 2, steps_per_lesson: int = 3, **course_kwargs) -> TrainingCourse:
    """Create a course with a full lesson/step tree, ordered predictably."""
    course = TrainingCourseFactory(**course_kwargs)
    for lesson_index in range(1, lessons + 1):
        lesson = TrainingLessonFactory(course=course, order=lesson_index)
        for step_index in range(1, steps_per_lesson + 1):
            TrainingStepFactory(lesson=lesson, order=step_index)
    return course
