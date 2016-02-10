import os
import arrow
import tarfile
import xml.etree.ElementTree as ET

from dateutil import tz


class MoodleEvent():
    """Describes an XML Moodle event with key based access"""
    def __init__(self, path):
        self.modified = False
        self.path = path
        self.tree = ET.parse(path)
        self.activity = self.tree.getroot()

        if len(self.activity) != 1:
            raise Exception('An activity can only have one event.')
        self.event = self.activity[0]

    def __getitem__(self, k):
        if k == 'id':
            return self.event.attrib[k]
        if k == 'moduleid':
            return int(self.activity.attrib[k])
        return self.event.find(k).text

    def __setitem__(self, k, v):
        if k == 'id' or k == 'moduleid':
            raise Exception('Not allowed')
        self.event.find(k).text = v
        self.modified = True

    def set_end_datetime(self, datetime):
        timestamp = str(arrow.get(datetime).to('utc').timestamp)
        k = self.event_keys['close']
        self.__setitem__(k, timestamp)

    def set_start_datetime(self, datetime):
        timestamp = str(arrow.get(datetime).to('utc').timestamp)
        k = self.event_keys['start']
        self.__setitem__(k, timestamp)

    def get_start_datetime(self):
        return self._get_start_arrow().datetime

    def get_start_timestamp(self):
        return self._get_start_arrow().timestamp

    def get_end_datetime(self):
        return self._get_end_arrow().datetime

    def get_end_timestamp(self):
        return self._get_end_arrow().timestamp

    def get_pretty_name(self):
        """To be implemented by subclasses"""
        raise Exception('Unimplemented')

    def write(self):
        if not self.modified:
            return
        self.tree.write(self.path, short_empty_elements=False, encoding='UTF-8',
                        xml_declaration=True)
        self._write_calendar()

    def _write_calendar(self):
        moodle_cal_path = os.path.join(self.global_path, 'calendar.xml')
        cal_tree = ET.parse(moodle_cal_path)
        events = cal_tree.getroot()

        if len(events) > 2 or len(events) < 1:
            raise Exception('Unimplemented')

        events[0].find('timestart').text = str(self.get_start_timestamp())
        events[0].find('timeduration').text = 0

        if len(events) > 1:
            events[0].find('timeduration').text = str(
                self.get_end_timestamp() - self.get_start_timestamp())

            events[1].find('timeduration').text = 0
            events[1].find('timestart').text = str(self.get_end_timestamp())

        cal_tree.write(moodle_cal_path, short_empty_elements=False,
                       encoding='UTF-8', xml_declaration=True)

    def _get_end_arrow(self):
        """Returns end as arrow object"""
        return self._get_arrow('close')

    def _get_start_arrow(self):
        """Returns end as arrow object"""
        return self._get_arrow('start')

    def _get_arrow(self, generic_event_key):
        """Gets the arrow object representation of the start or close event.
        generic_event_key: litteral string `start` or `close`
        The actal key of the XML is resolved with the `event_keys` of
        the subclasses."""
        k = self.event_keys[generic_event_key]
        epoch = self.event.find(k).text
        return arrow.get(epoch, tzinfo=tz.gettz('America/Montreal'))


class MoodleQuiz(MoodleEvent):
    """Describes an XML Moodle quiz with key based access"""

    event_keys = {
        'start': 'timeopen',
        'close': 'timeclose'
    }

    def __init__(self, path):
        self.global_path = path
        super().__init__(os.path.join(path, 'quiz.xml'))

    def get_pretty_name(self):
        return 'Quiz'


class MoodleHomework(MoodleEvent):
    """Describes an XML Moodle assignment (homework) with key based access"""

    event_keys = {
        'start': 'allowsubmissionsfromdate',
        'close': 'duedate'
    }

    def __init__(self, path):
        self.global_path = path
        super().__init__(os.path.join(path, 'assign.xml'))

    def get_pretty_name(self):
        return 'Homework'


class MoodleCourse():
    """\
    Describes a complete Moodle course from an unpacked archive on the disk"""

    modname_to_class = {
        'quiz': MoodleQuiz,
        'assign': MoodleHomework
        }

    def __init__(self, moodle_archive_path):
        self.path = moodle_archive_path
        self.fullpath = os.path.join(self.path, 'moodle_backup.xml')
        self.backup = ET.parse(self.fullpath)

        self._load_activities_and_sequence()

    def replace_event(self, activity):
        self.activities[type(activity)][activity.rel_id - 1] = activity

    def get_activity_by_type_and_num(self, type, relative_number):
        return self.activities[type][relative_number - 1]

    def write(self, output_path):
        self._write_activities_to_disk()

        # Moodle archives require special care !
        # Archive must be created like this `tar -cf archive.mbz *`
        ogwd = os.getcwd()
        os.chdir(self.path)
        full_output_path = os.path.join(ogwd, output_path)

        with tarfile.open(full_output_path, "w:gz") as archive:
            for name in os.listdir(self.path):
                archive.add(name)
            archive.close()
        os.chdir(ogwd)

    def _load_activity_sequence(self):
        """"Read the activity sequence from moodle_backup.xml.
        Returns a list of the module_ids in order of the course.
        """
        o = []
        activities = self.backup.getroot().find('information') \
            .find('contents').find('activities')

        for activity in activities:
            o.append(int(activity.find('moduleid').text))
        return o

    def _load_activities_and_sequence(self):
        self.activity_sequence = self._load_activity_sequence()
        self.activities = self._load_activites()

    def _load_activites(self):
        activities = {}
        for clazz in self.modname_to_class.values():
            activities[clazz] = []

        for a in self.backup.getroot().find('information').find('contents'). \
                find('activities'):
            module_name = a.find('modulename').text
            directory = a.find('directory').text

            if module_name not in self.modname_to_class:
                continue  # Ignore incomptatible activity

            clazz = self.modname_to_class[module_name]
            activities[clazz].append(clazz(os.path.join(self.path, directory)))

        for activity_type, items in activities.items():
            activities[activity_type] = self._sort_activity_type(items)

        return activities

    def _sort_activity_type(self, activities):
        s = sorted(activities, key=lambda activity:
                   self.activity_sequence.index(activity['moduleid']))
        # Set relative id of activity
        for i, activity in enumerate(s):
            activity.rel_id = i + 1
        return s

    def _write_activities_to_disk(self):
        for activities in self.activities.values():
            for activity in activities:
                activity.write()
