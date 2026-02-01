"""
Адаптер для конвертации данных из SQLAlchemy в формат для алгоритмов генерации
"""

from typing import Dict, List, Optional
from collections import defaultdict
from schedule_base import (
    Teacher as AlgoTeacher,
    Classroom as AlgoClassroom,
    Subject as AlgoSubject,
    Student as AlgoStudent,
    Class as AlgoClass,
    EGEPracticeGroup,
    TimeSlot,
    DayOfWeek,
    SubjectType,
    Schedule as AlgoSchedule,
    Lesson as AlgoLesson
)
from models import (
    db, Teacher, Classroom, Subject, SchoolClass, Student, Workload, Schedule, Lesson
)


# Маппинг дней недели (БД: 0-4 -> Enum: 1-5)
DAY_MAP = {
    0: DayOfWeek.MONDAY,
    1: DayOfWeek.TUESDAY,
    2: DayOfWeek.WEDNESDAY,
    3: DayOfWeek.THURSDAY,
    4: DayOfWeek.FRIDAY
}

DAY_REVERSE_MAP = {v: k for k, v in DAY_MAP.items()}


class DatabaseDataLoader:
    """
    Загрузчик данных из базы SQLAlchemy для алгоритмов генерации расписания.

    Конвертирует SQLAlchemy модели в dataclass'ы из schedule_base.py
    """

    def __init__(self):
        self.teachers: Dict[str, AlgoTeacher] = {}
        self.classrooms: Dict[str, AlgoClassroom] = {}
        self.subjects: List[AlgoSubject] = []
        self.classes: Dict[str, AlgoClass] = {}
        self.students: List[AlgoStudent] = []
        self.ege_groups: List[EGEPracticeGroup] = []

        # Маппинг ID -> объекты (для быстрого доступа)
        self._teacher_by_id: Dict[int, AlgoTeacher] = {}
        self._classroom_by_id: Dict[int, AlgoClassroom] = {}
        self._subject_by_id: Dict[int, Subject] = {}  # SQLAlchemy Subject
        self._class_by_id: Dict[int, AlgoClass] = {}

    def load_all(self):
        """Загрузить все данные из базы"""
        self._load_teachers()
        self._load_classrooms()
        self._load_classes()
        self._load_students()
        self._load_subjects()
        self._load_ege_groups()

        print(f"[DatabaseDataLoader] Загружено:")
        print(f"  - Учителей: {len(self.teachers)}")
        print(f"  - Кабинетов: {len(self.classrooms)}")
        print(f"  - Классов: {len(self.classes)}")
        print(f"  - Учеников: {len(self.students)}")
        print(f"  - Предметов: {len(self.subjects)}")
        print(f"  - Групп ЕГЭ: {len(self.ege_groups)}")

    def _load_teachers(self):
        """Загрузить учителей"""
        db_teachers = Teacher.query.all()

        for t in db_teachers:
            # Конвертируем недоступные дни
            unavailable_days = set()
            for day_num in t.get_unavailable_days():
                if day_num in DAY_MAP:
                    unavailable_days.add(DAY_MAP[day_num])

            # Получаем номер домашнего кабинета
            home_classroom = None
            if t.home_classroom:
                home_classroom = t.home_classroom.number

            algo_teacher = AlgoTeacher(
                name=t.name,
                subjects=[s.name for s in t.subjects],
                home_classroom=home_classroom,
                unavailable_days=unavailable_days
            )

            self.teachers[t.name] = algo_teacher
            self._teacher_by_id[t.id] = algo_teacher

    def _load_classrooms(self):
        """Загрузить кабинеты"""
        db_classrooms = Classroom.query.all()

        for c in db_classrooms:
            # Найти ответственного учителя
            responsible = None
            for t in c.responsible_teachers:
                responsible = t.name
                break

            algo_classroom = AlgoClassroom(
                number=c.number,
                capacity=c.capacity or 30,
                floor=c.floor or 1,
                responsible_teacher=responsible
            )

            self.classrooms[c.number] = algo_classroom
            self._classroom_by_id[c.id] = algo_classroom

    def _load_classes(self):
        """Загрузить классы"""
        db_classes = SchoolClass.query.all()

        for c in db_classes:
            algo_class = AlgoClass(
                name=c.name,
                profile=c.profile or "",
                students=[]  # Заполняется в _load_students
            )

            self.classes[c.name] = algo_class
            self._class_by_id[c.id] = algo_class

    def _load_students(self):
        """Загрузить учеников"""
        db_students = Student.query.all()

        for s in db_students:
            ege_subjects = [subj.name for subj in s.ege_subjects]
            class_name = s.school_class.name if s.school_class else "Unknown"

            algo_student = AlgoStudent(
                name=s.name,
                class_name=class_name,
                ege_subjects=ege_subjects
            )

            self.students.append(algo_student)

            # Добавляем в класс
            if class_name in self.classes:
                self.classes[class_name].students.append(algo_student)

    def _load_subjects(self):
        """Загрузить предметы на основе нагрузки"""
        db_workloads = Workload.query.all()

        # Группируем нагрузку по (предмет, учитель, класс)
        workload_map = defaultdict(list)

        for w in db_workloads:
            key = (w.subject_id, w.teacher_id)
            workload_map[key].append(w)

        for (subject_id, teacher_id), workloads in workload_map.items():
            db_subject = Subject.query.get(subject_id)
            db_teacher = Teacher.query.get(teacher_id)

            if not db_subject or not db_teacher:
                continue

            # Получаем список классов для этой нагрузки
            classes = []
            total_hours = 0

            for w in workloads:
                if w.school_class:
                    classes.append(w.school_class.name)
                total_hours += w.hours_per_week

            # Получаем учителя из алгоритмов
            algo_teacher = self._teacher_by_id.get(teacher_id)
            if not algo_teacher:
                continue

            # Определяем тип предмета
            subject_type = SubjectType.MANDATORY
            if db_subject.is_ege:
                subject_type = SubjectType.EGE_PRACTICE

            algo_subject = AlgoSubject(
                name=db_subject.name,
                subject_type=subject_type,
                hours_per_week=total_hours,
                teacher=algo_teacher,
                classes=classes,
                is_grouped=any(w.is_group for w in workloads),
                groups=[]
            )

            self.subjects.append(algo_subject)
            self._subject_by_id[subject_id] = db_subject

    def _load_ege_groups(self):
        """Загрузить группы ЕГЭ практикумов"""
        # Находим предметы с практикумами ЕГЭ
        ege_subjects = Subject.query.filter_by(is_ege=True).all()

        for subject in ege_subjects:
            if subject.ege_hours <= 0:
                continue

            # Находим учеников, сдающих этот предмет
            students_for_subject = []
            for student in self.students:
                if subject.name in student.ege_subjects:
                    students_for_subject.append(student)

            if not students_for_subject:
                continue

            # Находим учителя для этого предмета
            teachers_for_subject = list(subject.teachers)
            if not teachers_for_subject:
                continue

            # Берем первого доступного учителя
            teacher = self._teacher_by_id.get(teachers_for_subject[0].id)
            if not teacher:
                continue

            ege_group = EGEPracticeGroup(
                subject=subject.name,
                teacher=teacher,
                students=students_for_subject,
                hours_per_week=subject.ege_hours
            )

            self.ege_groups.append(ege_group)


def convert_algo_schedule_to_db(
    algo_schedule: AlgoSchedule,
    db_schedule: Schedule,
    loader: DatabaseDataLoader
) -> int:
    """
    Конвертировать расписание из формата алгоритмов в записи БД.

    Args:
        algo_schedule: Расписание из алгоритмов
        db_schedule: Модель Schedule в БД
        loader: Загрузчик данных (для маппинга)

    Returns:
        Количество созданных уроков
    """
    # Удаляем старые уроки
    Lesson.query.filter_by(schedule_id=db_schedule.id).delete()

    # Создаем маппинг имён -> ID
    teacher_id_map = {t.name: t.id for t in Teacher.query.all()}
    classroom_id_map = {c.number: c.id for c in Classroom.query.all()}
    subject_id_map = {s.name: s.id for s in Subject.query.all()}
    class_id_map = {c.name: c.id for c in SchoolClass.query.all()}

    lessons_created = 0

    for algo_lesson in algo_schedule.lessons:
        # Находим ID учителя
        teacher_id = teacher_id_map.get(algo_lesson.teacher.name)
        if not teacher_id:
            print(f"  [WARN] Учитель не найден: {algo_lesson.teacher.name}")
            continue

        # Находим ID предмета
        # Для практикумов ЕГЭ название может быть "Практикум ЕГЭ: Математика"
        subject_name = algo_lesson.subject
        if subject_name.startswith("Практикум ЕГЭ: "):
            subject_name = subject_name.replace("Практикум ЕГЭ: ", "")

        subject_id = subject_id_map.get(subject_name)
        if not subject_id:
            # Пробуем найти частичное совпадение
            for name, sid in subject_id_map.items():
                if subject_name in name or name in subject_name:
                    subject_id = sid
                    break

        if not subject_id:
            print(f"  [WARN] Предмет не найден: {subject_name}")
            continue

        # Находим ID кабинета
        classroom_id = None
        if algo_lesson.classroom:
            classroom_id = classroom_id_map.get(algo_lesson.classroom.number)

        # Находим ID класса
        class_id = None
        class_or_group = algo_lesson.class_or_group

        if class_or_group and not class_or_group.startswith("ЕГЭ-"):
            class_id = class_id_map.get(class_or_group)

        # Конвертируем день
        day = DAY_REVERSE_MAP.get(algo_lesson.time_slot.day, 0)

        # Создаем урок в БД
        db_lesson = Lesson(
            schedule_id=db_schedule.id,
            subject_id=subject_id,
            teacher_id=teacher_id,
            class_id=class_id,
            classroom_id=classroom_id,
            day=day,
            lesson_number=algo_lesson.time_slot.lesson_number,
            is_ege_practice=algo_lesson.is_ege_practice,
            group_name=class_or_group if algo_lesson.is_ege_practice else None
        )

        db.session.add(db_lesson)
        lessons_created += 1

    db.session.commit()
    return lessons_created


def calculate_schedule_stats(db_schedule: Schedule) -> dict:
    """
    Рассчитать статистику расписания для отображения.

    Returns:
        dict с ключами: lessons_placed, success_rate, conflicts, quality_score
    """
    lessons = db_schedule.lessons.all()
    total_lessons = len(lessons)

    if total_lessons == 0:
        return {
            'lessons_placed': 0,
            'success_rate': 0,
            'conflicts': 0,
            'quality_score': 0
        }

    # Подсчет конфликтов
    conflicts = 0

    # Проверяем конфликты учителей
    teacher_slots = defaultdict(list)
    for lesson in lessons:
        key = (lesson.teacher_id, lesson.day, lesson.lesson_number)
        teacher_slots[key].append(lesson)

    for key, slot_lessons in teacher_slots.items():
        if len(slot_lessons) > 1:
            conflicts += len(slot_lessons) - 1

    # Проверяем конфликты классов
    class_slots = defaultdict(list)
    for lesson in lessons:
        if lesson.class_id:
            key = (lesson.class_id, lesson.day, lesson.lesson_number)
            class_slots[key].append(lesson)

    for key, slot_lessons in class_slots.items():
        if len(slot_lessons) > 1:
            conflicts += len(slot_lessons) - 1

    # Подсчет окон у учителей
    teacher_gaps = 0
    teacher_lessons = defaultdict(lambda: defaultdict(list))

    for lesson in lessons:
        teacher_lessons[lesson.teacher_id][lesson.day].append(lesson.lesson_number)

    for teacher_id, days in teacher_lessons.items():
        for day, lesson_nums in days.items():
            if len(lesson_nums) >= 2:
                lesson_nums_sorted = sorted(lesson_nums)
                for i in range(len(lesson_nums_sorted) - 1):
                    gap = lesson_nums_sorted[i + 1] - lesson_nums_sorted[i] - 1
                    teacher_gaps += gap

    # Рассчитываем требуемое количество уроков из нагрузки
    total_required = sum(w.hours_per_week for w in Workload.query.all())
    success_rate = min(100, int(total_lessons / max(total_required, 1) * 100))

    # Оценка качества (100 - штрафы)
    quality_score = max(0, 100 - conflicts * 5 - teacher_gaps * 2)

    return {
        'lessons_placed': total_lessons,
        'success_rate': success_rate,
        'conflicts': conflicts,
        'quality_score': quality_score
    }
