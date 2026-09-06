"""Mandatory modern-lane ARM positives, explicitly selected outside host discovery."""

from scripts.workflow_pilot.tests.test_review_subjects import SubjectTestCase


class ArmSubjectTests(SubjectTestCase):
    def assert_complete_aoe(self, members, revision):
        observations = self.run_members(members, revision)
        self.assert_satisfied(observations)
        self.assertEqual({item.kind for item in observations}, {"native", "arm-object"})
        self.assertEqual(
            {item.obligation.member: item.checks for item in observations
             if item.kind == "arm-object"},
            {"enabled:objects": 2, "disabled:objects": 1})

    def test_complete_aoe_subject_has_native_and_arm_objects(self):
        self.assert_complete_aoe(self.tools.members(self.scope("aoe")), self.repo.base)

    def test_semantics_preserving_source_refactor_remains_green(self):
        path = "src/expansion_aoe.c"
        source = (self.repo.root / path).read_text()
        revision = self.repo.commit({path: "/* Formatting-only review control. */\n" + source})
        members = self.tools.members(self.scope("aoe", revision), (self.repo.base,))
        self.assert_complete_aoe(members, revision)
