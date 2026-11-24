"""An authored reporting chain must survive the usual direction-to-film boundary."""
import unittest
from wf1_short import film_direction


class ReportingExample(unittest.TestCase):
    def test_director_example_reaches_composition(self):
        example = dict(first=0, last=24, speaker="Interview guest",
                       question="Who reports to whom?", finding="One team has several layers",
                       finding_at=3, flat_at=21, levels=[dict(at=6,label="Person"),
                       dict(at=8,label="Manager")], counts=[dict(at=14,value="3")])
        self.assertEqual(film_direction(dict(reporting_example=example)).get("reporting_example"), example)

    def test_answer_starts_with_its_clause_and_count_order_does_not_imply_a_link(self):
        from wf1_reporting import example
        direction = dict(first=0, last=30, speaker="Guest", question="Who reports to whom?",
                         finding="One group has several layers", finding_at=8, flat_at=26,
                         levels=[dict(at=12,label="Person"),dict(at=15,label="Manager")],
                         counts=[dict(at=19,value=3),dict(at=22,value=5,level_at=15)])
        cue = dict(first=4,last=8,speaker="Guest")
        result = example(direction,31,[cue])
        self.assertEqual(result["finding_at"],4)
        self.assertEqual(result["counts"],[dict(at=19,value="3"),dict(at=22,value="5",level_at=15)])
