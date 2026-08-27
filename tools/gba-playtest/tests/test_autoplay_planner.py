"""Focused contract checks for issue #92's local planner bridge."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import unittest
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from unittest import mock


TESTS_DIR = Path(__file__).resolve().parent
PLAYTEST_DIR = TESTS_DIR.parent
for path in (str(PLAYTEST_DIR), str(TESTS_DIR)):
    if path not in sys.path:
        sys.path.insert(0, path)

import autoplay_planner as planner
import host_mode
from homebrew_fixture import (
    build_planner_transport_backend,
    build_planner_transport_ack_driver,
    build_production_planner_rom,
)


PROVENANCE = {
    "config": "modern-debug",
    "rom": {"sha1": "fixture", "size": 1024},
    "scenario": {"name": "two-chapter", "schema_version": 1},
}
TRANSCRIPT_SESSION = {
    "transport": "fixture",
    "rom_identity": 0,
    "config_identity": 0,
    "scenario_identity": 0,
    "seed_identity": 0,
    "ready_run_id": 0,
    "run_id": 1,
}


class PlannerBridgeTests(unittest.TestCase):
    def test_scripted_and_search_planners_replay_two_chapters_without_save_state(self):
        scripted = planner.run_two_chapter_replay(planner.ScriptedPlanner(), PROVENANCE)
        searched = planner.run_two_chapter_replay(planner.BoundedSearchPlanner(), PROVENANCE)
        self.assertEqual(scripted["terminal"], "success")
        self.assertEqual(searched["terminal"], "success")
        self.assertEqual(scripted["campaign_checkpoint"]["chapter"], 2)
        self.assertEqual(searched["campaign_checkpoint"]["inventory"], ("fixture-key",))
        self.assertEqual(
            scripted["campaign_checkpoint"]["semantic_state_digest"],
            searched["campaign_checkpoint"]["semantic_state_digest"],
        )
        changed = dict(scripted["campaign_checkpoint"])
        changed.pop("semantic_state_digest")
        changed["resources"] = {"gold": 999}
        self.assertNotEqual(
            scripted["campaign_checkpoint"]["semantic_state_digest"],
            planner.semantic_state_digest(changed),
        )
        self.assertEqual(scripted["trace_digest"], planner.run_two_chapter_replay(
            planner.ScriptedPlanner(), PROVENANCE
        )["trace_digest"])
        self.assertEqual(len(json.dumps(scripted, sort_keys=True)), len(json.dumps(searched, sort_keys=True)))

    def test_mailbox_rejects_stale_unknown_forged_and_cancelled_requests(self):
        bridge = planner.PlannerBridge(PROVENANCE)
        run_id = bridge.begin(PROVENANCE)
        observation = bridge.observe(
            1,
            (planner.Field("map", "gBmMapTerrain", 4096, planner.Availability.AVAILABLE, 1),),
            (planner.Action("MOVE_WAIT", 1, (1, 1)),),
        )
        with self.assertRaisesRegex(planner.PlannerError, planner.Rejection.STALE_OBSERVATION.value):
            bridge.commit(planner.Command(planner.CommandKind.COMMIT, run_id, 99, 0, "forged"))
        with self.assertRaisesRegex(planner.PlannerError, planner.Rejection.UNKNOWN_ACTION.value):
            bridge.commit(planner.Command(planner.CommandKind.COMMIT, run_id, observation.observation_id, 1, "forged"))
        with self.assertRaisesRegex(planner.PlannerError, planner.Rejection.TOKEN_MISMATCH.value):
            bridge.commit(planner.Command(planner.CommandKind.COMMIT, run_id, observation.observation_id, 0, "forged"))
        self.assertEqual(
            tuple(event["event"] for event in bridge.trace),
            ("session", "observation_complete", "settled"),
        )
        with self.assertRaisesRegex(planner.PlannerError, planner.Rejection.CANCELLED.value):
            bridge.commit(planner.Command(planner.CommandKind.CANCEL, run_id, observation.observation_id))
        self.assertTrue(bridge.cancelled)

    def test_bounds_availability_pages_and_provenance_fail_closed(self):
        bridge = planner.PlannerBridge(PROVENANCE)
        with self.assertRaisesRegex(planner.PlannerError, "provenance mismatch"):
            bridge.begin({**PROVENANCE, "config": "modern-release"})
        bridge.begin(PROVENANCE)
        unavailable = planner.Field(
            "objective", "chapter objectives", 32, planner.Availability.UNSUPPORTED_RULE, None
        )
        actions = tuple(planner.Action("MOVE_WAIT", 1, (index, 0)) for index in range(41))
        observation = bridge.observe(1, (unavailable,), actions)
        complete = planner.collect_observation_pages(bridge, observation)
        self.assertEqual(len(complete.actions), 41)
        self.assertEqual(complete.actions[-1].ordinal, 40)
        bounded = planner.PlannerBridge(PROVENANCE)
        bounded.begin(PROVENANCE)
        maximum = bounded.observe(
            1,
            (),
            tuple(
                planner.Action("MOVE_WAIT", 1, (index, 0))
                for index in range(512)
            ),
        )
        maximum_complete = planner.collect_observation_pages(bounded, maximum)
        self.assertEqual(maximum.page_count, 25)
        self.assertEqual(maximum_complete.actions[-1].ordinal, 511)
        with self.assertRaisesRegex(planner.PlannerError, "resource limit"):
            overflow = planner.PlannerBridge(PROVENANCE)
            overflow.begin(PROVENANCE)
            overflow.observe(
                1,
                (),
                tuple(planner.Action("MOVE_WAIT", 1, (0, 0)) for _ in range(513)),
            )

    def test_public_validation_errors_name_protocol_v2(self):
        bridge = planner.PlannerBridge(PROVENANCE)
        bridge.begin(PROVENANCE)
        with self.assertRaisesRegex(
            planner.PlannerError,
            r"chapter is outside the v2 range",
        ):
            bridge.observe(0, (), (planner.Action("MOVE_WAIT", 1, (0, 0)),))

        bridge = planner.PlannerBridge(PROVENANCE)
        bridge.begin(PROVENANCE)
        with self.assertRaisesRegex(
            planner.PlannerError,
            r"legal action count exceeds v2 resource limit",
        ):
            bridge.observe(
                1,
                (),
                tuple(
                    planner.Action("MOVE_WAIT", 1, (0, 0))
                    for _ in range(planner.MAX_ACTIONS + 1)
                ),
            )

    def test_transcript_round_trip_tamper_order_and_atomic_limits(self):
        bridge = planner.PlannerBridge(PROVENANCE)
        run_id = bridge.begin(PROVENANCE)
        observation = bridge.observe(
            1,
            (
                planner.Field(
                    "gold",
                    "gPlaySt.partyGoldAmount",
                    0xFFFFFFFF,
                    planner.Availability.AVAILABLE,
                    100,
                ),
            ),
            tuple(
                planner.Action("MOVE_WAIT", 1, (index + 1, 0))
                for index in range(23)
            ),
        )
        complete = planner.collect_observation_pages(bridge, observation)
        selected = complete.actions[0]
        bridge.commit(
            planner.Command(
                planner.CommandKind.COMMIT,
                run_id,
                observation.observation_id,
                selected.ordinal,
                selected.token,
            )
        )
        exported = bridge.transcript.export()
        self.assertLessEqual(len(exported), planner.MAX_TRACE_BYTES)
        imported = planner.PlannerTranscript.import_bytes(exported)
        self.assertEqual(imported.export(), exported)
        self.assertEqual(imported.digest(), bridge.transcript.digest())

        def rechain(document):
            previous = "0" * 64
            for sequence, event in enumerate(document["events"]):
                event.pop("event_digest", None)
                event["sequence"] = sequence
                event["previous_digest"] = previous
                event["event_digest"] = planner._digest(event)
                previous = event["event_digest"]

        empty = {
            "schema": planner.PlannerTranscript.SCHEMA,
            "events": [],
        }
        with self.assertRaisesRegex(
            planner.PlannerError,
            "exactly one leading session",
        ):
            planner.PlannerTranscript.import_bytes(
                planner._canonical(empty)
            )

        sessionless = json.loads(exported)
        sessionless["events"].pop(0)
        rechain(sessionless)
        with self.assertRaisesRegex(
            planner.PlannerError,
            "exactly one leading session",
        ):
            planner.PlannerTranscript.import_bytes(
                planner._canonical(sessionless)
            )

        missing_provenance = json.loads(exported)
        missing_provenance["events"][0].pop("provenance")
        rechain(missing_provenance)
        with self.assertRaisesRegex(
            planner.PlannerError,
            "invalid planner transcript session provenance",
        ):
            planner.PlannerTranscript.import_bytes(
                planner._canonical(missing_provenance)
            )

        late_session = json.loads(exported)
        late_session["events"][0], late_session["events"][1] = (
            late_session["events"][1],
            late_session["events"][0],
        )
        rechain(late_session)
        with self.assertRaisesRegex(
            planner.PlannerError,
            "exactly one leading session",
        ):
            planner.PlannerTranscript.import_bytes(
                planner._canonical(late_session)
            )

        duplicate_session = json.loads(exported)
        duplicate_session["events"].insert(
            1,
            dict(duplicate_session["events"][0]),
        )
        rechain(duplicate_session)
        with self.assertRaisesRegex(
            planner.PlannerError,
            "exactly one leading session",
        ):
            planner.PlannerTranscript.import_bytes(
                planner._canonical(duplicate_session)
            )

        for field in (
            "rom_identity",
            "config_identity",
            "scenario_identity",
            "seed_identity",
        ):
            with self.subTest(session_identity=field):
                provenance_tampered = json.loads(exported)
                provenance_tampered["events"][0]["provenance"][
                    field
                ] ^= 1
                rechain(provenance_tampered)
                with self.assertRaisesRegex(
                    planner.PlannerError,
                    "observation session "
                    "(identity|scenario/seed) mismatch",
                ):
                    planner.PlannerTranscript.import_bytes(
                        planner._canonical(provenance_tampered)
                    )

        run_tampered = json.loads(exported)
        provenance = run_tampered["events"][0]["provenance"]
        provenance["ready_run_id"] += 1
        provenance["run_id"] += 1
        rechain(run_tampered)
        with self.assertRaisesRegex(
            planner.PlannerError,
            "(observation session|accepted command run) identity mismatch",
        ):
            planner.PlannerTranscript.import_bytes(
                planner._canonical(run_tampered)
            )

        tampered = json.loads(exported)
        complete_event = next(
            event
            for event in tampered["events"]
            if event["event"] == "observation_complete"
        )
        complete_event["observation"]["actions"][0]["ordinal"] = 7
        with self.assertRaisesRegex(
            planner.PlannerError,
            "digest mismatch",
        ):
            planner.PlannerTranscript.import_bytes(
                planner._canonical(tampered)
            )

        identity_tampered = json.loads(exported)
        complete_event = next(
            event
            for event in identity_tampered["events"]
            if event["event"] == "observation_complete"
        )
        complete_event["page_identity"][3] += 1
        rechain(identity_tampered)
        with self.assertRaisesRegex(
            planner.PlannerError,
            "page identity mismatch",
        ):
            planner.PlannerTranscript.import_bytes(
                planner._canonical(identity_tampered)
            )

        token_tampered = json.loads(exported)
        complete_event = next(
            event
            for event in token_tampered["events"]
            if event["event"] == "observation_complete"
        )
        complete_event["observation"]["actions"][0]["token"]["word3"] ^= 1
        complete_event["candidate_set_digest"] = planner._digest(
            complete_event["observation"]["actions"]
        )
        complete_index = token_tampered["events"].index(complete_event)
        token_tampered["events"][complete_index + 1][
            "observation_digest"
        ] = planner._digest(complete_event["observation"])
        rechain(token_tampered)
        with self.assertRaisesRegex(
            planner.PlannerError,
            "accepted transcript token mismatch",
        ):
            planner.PlannerTranscript.import_bytes(
                planner._canonical(token_tampered)
            )

        runtime_tampered = json.loads(exported)
        settled_event = next(
            event
            for event in runtime_tampered["events"]
            if event["event"] == "settled"
        )
        settled_event["terminal"]["state"] ^= 1
        rechain(runtime_tampered)
        with self.assertRaisesRegex(
            planner.PlannerError,
            "settled runtime state mismatch",
        ):
            planner.PlannerTranscript.import_bytes(
                planner._canonical(runtime_tampered)
            )

        acknowledgement = next(
            event
            for event in json.loads(exported)["events"]
            if event["event"] == "acknowledgement"
        )
        for result, rejection in (
            (0, 0),
            (1, 4),
            (2, 0),
            (0, 99),
        ):
            with self.subTest(
                acknowledgement_result=result,
                acknowledgement_rejection=rejection,
            ):
                invalid_ack = json.loads(exported)
                event = next(
                    item
                    for item in invalid_ack["events"]
                    if item["event"] == "acknowledgement"
                )
                event["result"] = result
                event["rejection"] = rejection
                rechain(invalid_ack)
                with self.assertRaisesRegex(
                    planner.PlannerError,
                    "invalid acknowledgement result/rejection pair",
                ):
                    planner.PlannerTranscript.import_bytes(
                        planner._canonical(invalid_ack)
                    )

        for field, value, message in (
            ("command_id", acknowledgement["command_id"] + 1,
             "acknowledgement order"),
            ("kind", acknowledgement["kind"] + 1,
             "acknowledgement kind mismatch"),
        ):
            with self.subTest(acknowledgement_field=field):
                invalid_ack = json.loads(exported)
                event = next(
                    item
                    for item in invalid_ack["events"]
                    if item["event"] == "acknowledgement"
                )
                event[field] = value
                rechain(invalid_ack)
                with self.assertRaisesRegex(
                    planner.PlannerError,
                    message,
                ):
                    planner.PlannerTranscript.import_bytes(
                        planner._canonical(invalid_ack)
                    )

        rejected_commit = json.loads(exported)
        acknowledgement = next(
            event
            for event in rejected_commit["events"]
            if event["event"] == "acknowledgement"
                and event["kind"] == 2
        )
        acknowledgement["result"] = 0
        acknowledgement["rejection"] = 4
        rechain(rejected_commit)
        with self.assertRaisesRegex(
            planner.PlannerError,
            "settled rejection does not match acknowledgement",
        ):
            planner.PlannerTranscript.import_bytes(
                planner._canonical(rejected_commit)
            )

        committed_rejection = json.loads(exported)
        acknowledgement_index = next(
            index
            for index, event in enumerate(committed_rejection["events"])
            if event["event"] == "acknowledgement"
                and event["kind"] == 2
        )
        acknowledgement = committed_rejection["events"][
            acknowledgement_index
        ]
        acknowledgement["result"] = 0
        acknowledgement["rejection"] = 4
        observation_event = next(
            event
            for event in committed_rejection["events"][
                acknowledgement_index + 1 :
            ]
            if event["event"] == "observation_page"
        )
        observation_event["observation"]["rejection"] = 4
        settled_event = next(
            event
            for event in committed_rejection["events"][
                acknowledgement_index + 1 :
            ]
            if event["event"] == "settled"
        )
        settled_event["terminal"]["rejection"] = 4
        settled_event["observation_digest"] = planner._digest(
            observation_event["observation"]
        )
        rechain(committed_rejection)
        with self.assertRaisesRegex(
            planner.PlannerError,
            "rejected COMMIT cannot settle as COMMITTED",
        ):
            planner.PlannerTranscript.import_bytes(
                planner._canonical(committed_rejection)
            )

        for command_kind in (
            planner.CommandKind.PAGE.value,
            planner.CommandKind.COMMIT.value,
        ):
            for observation_id in (0, 2):
                with self.subTest(
                    command_kind=command_kind,
                    observation_id=observation_id,
                ):
                    stale_command = json.loads(exported)
                    command = next(
                        event["command"]
                        for event in stale_command["events"]
                        if event["event"] == "command"
                            and event["command"]["kind"] == command_kind
                    )
                    command["observation_id"] = observation_id
                    rechain(stale_command)
                    with self.assertRaisesRegex(
                        planner.PlannerError,
                        "command observation identity mismatch",
                    ):
                        planner.PlannerTranscript.import_bytes(
                            planner._canonical(stale_command)
                        )

        page_cross_swap = json.loads(exported)
        page_commands = [
            event["command"]
            for event in page_cross_swap["events"]
            if event["event"] == "command"
                and event["command"]["kind"]
                    == planner.CommandKind.PAGE.value
        ]
        page_commands[0]["page_index"], page_commands[1]["page_index"] = (
            page_commands[1]["page_index"],
            page_commands[0]["page_index"],
        )
        rechain(page_cross_swap)
        with self.assertRaisesRegex(
            planner.PlannerError,
            "PAGE response identity mismatch",
        ):
            planner.PlannerTranscript.import_bytes(
                planner._canonical(page_cross_swap)
            )

        base_events = json.loads(exported)["events"]
        command_index = next(
            index
            for index, event in enumerate(base_events)
            if event["event"] == "command"
                and event["command"]["kind"]
                    == planner.CommandKind.PAGE.value
        )
        ack_index = command_index + 1
        completion_index = command_index + 2
        response_index = command_index + 3

        completion_before_ack = json.loads(exported)
        events = completion_before_ack["events"]
        events[ack_index], events[completion_index] = (
            events[completion_index],
            events[ack_index],
        )
        rechain(completion_before_ack)
        with self.assertRaisesRegex(
            planner.PlannerError,
            "completion order",
        ):
            planner.PlannerTranscript.import_bytes(
                planner._canonical(completion_before_ack)
            )

        response_before_completion = json.loads(exported)
        events = response_before_completion["events"]
        events[completion_index], events[response_index] = (
            events[response_index],
            events[completion_index],
        )
        rechain(response_before_completion)
        with self.assertRaisesRegex(
            planner.PlannerError,
            "response observation precedes completion",
        ):
            planner.PlannerTranscript.import_bytes(
                planner._canonical(response_before_completion)
            )

        duplicate_ack = json.loads(exported)
        duplicate_ack["events"].insert(
            completion_index,
            dict(duplicate_ack["events"][ack_index]),
        )
        rechain(duplicate_ack)
        with self.assertRaisesRegex(
            planner.PlannerError,
            "acknowledgement order",
        ):
            planner.PlannerTranscript.import_bytes(
                planner._canonical(duplicate_ack)
            )

        duplicate_completion = json.loads(exported)
        duplicate_completion["events"].insert(
            response_index,
            dict(duplicate_completion["events"][completion_index]),
        )
        rechain(duplicate_completion)
        with self.assertRaisesRegex(
            planner.PlannerError,
            "completion order",
        ):
            planner.PlannerTranscript.import_bytes(
                planner._canonical(duplicate_completion)
            )

        interleaved_command = json.loads(exported)
        second_command = next(
            event
            for event in interleaved_command["events"][
                command_index + 1 :
            ]
            if event["event"] == "command"
        )
        interleaved_command["events"].insert(
            ack_index,
            dict(second_command),
        )
        rechain(interleaved_command)
        with self.assertRaisesRegex(
            planner.PlannerError,
            "command overlap",
        ):
            planner.PlannerTranscript.import_bytes(
                planner._canonical(interleaved_command)
            )

        missing_response = json.loads(exported)
        missing_response["events"].pop(response_index)
        rechain(missing_response)
        with self.assertRaisesRegex(
            planner.PlannerError,
            "settled event has no response observation",
        ):
            planner.PlannerTranscript.import_bytes(
                planner._canonical(missing_response)
            )

        reordered = json.loads(exported)
        reordered["events"][0], reordered["events"][1] = (
            reordered["events"][1],
            reordered["events"][0],
        )
        with self.assertRaisesRegex(
            planner.PlannerError,
            "exactly one leading session|order is invalid|digest mismatch",
        ):
            planner.PlannerTranscript.import_bytes(
                planner._canonical(reordered)
            )
        with self.assertRaisesRegex(
            planner.PlannerError,
            "invalid planner transcript JSON|not canonical",
        ):
            planner.PlannerTranscript.import_bytes(exported[:-1])

        bounded = planner.PlannerTranscript(max_bytes=512)
        bounded.record_session(TRANSCRIPT_SESSION)
        before = bounded.export()
        with self.assertRaisesRegex(
            planner.PlannerError,
            planner.Rejection.RESOURCE_LIMIT.value,
        ):
            bounded.record_complete_observation(complete)
        self.assertEqual(bounded.export(), before)

        probe = planner.PlannerTranscript()
        probe.record_session(TRANSCRIPT_SESSION)
        probe.record_complete_observation(complete)
        atomic = planner.PlannerTranscript(max_bytes=len(probe.export()))
        atomic.record_session(TRANSCRIPT_SESSION)
        atomic_before = atomic.export()
        with self.assertRaisesRegex(
            planner.PlannerError,
            planner.Rejection.RESOURCE_LIMIT.value,
        ):
            atomic.record_complete_and_settled(
                complete,
                (0,) * 13,
                (0,) * 16,
            )
        self.assertEqual(atomic.export(), atomic_before)

        second_token = planner._fixture_action_token(
            run_id,
            observation.observation_id,
            1,
            complete.actions[1].action,
        )
        self.assertEqual(len(set(selected.token.words)), 4)
        self.assertTrue(
            all(
                left != right
                for left, right in zip(
                    selected.token.words,
                    second_token.words,
                )
            )
        )

    def test_transcript_json_depth_and_recursion_fail_closed(self):
        def nested_array(depth):
            value = 0
            for _ in range(depth):
                value = [value]
            return value

        def nested_object(depth):
            value = 0
            for _ in range(depth):
                value = {"value": value}
            return value

        for factory in (nested_array, nested_object):
            for depth in (
                planner.MAX_JSON_DEPTH - 1,
                planner.MAX_JSON_DEPTH,
            ):
                with self.subTest(
                    shape=factory.__name__,
                    depth=depth,
                ):
                    encoded = planner._canonical(factory(depth))
                    self.assertIsNotNone(json.loads(encoded))
            with self.assertRaisesRegex(
                planner.PlannerError,
                "invalid planner transcript JSON depth",
            ):
                planner._canonical(
                    factory(planner.MAX_JSON_DEPTH + 1)
                )

        at_limit = (
            b"[" * planner.MAX_JSON_DEPTH
            + b"0"
            + b"]" * planner.MAX_JSON_DEPTH
        )
        with self.assertRaisesRegex(
            planner.PlannerError,
            "invalid planner transcript envelope",
        ):
            planner.PlannerTranscript.import_bytes(at_limit)
        above_limit = (
            b"[" * (planner.MAX_JSON_DEPTH + 1)
            + b"0"
            + b"]" * (planner.MAX_JSON_DEPTH + 1)
        )
        with self.assertRaisesRegex(
            planner.PlannerError,
            "invalid planner transcript JSON depth",
        ):
            planner.PlannerTranscript.import_bytes(above_limit)
        object_above_limit = (
            b'{"value":' * (planner.MAX_JSON_DEPTH + 1)
            + b"0"
            + b"}" * (planner.MAX_JSON_DEPTH + 1)
        )
        factory_calls = 0

        def transport_factory():
            nonlocal factory_calls
            factory_calls += 1
            raise AssertionError("invalid transcript started transport")

        with self.assertRaisesRegex(
            planner.PlannerError,
            "invalid planner transcript JSON depth",
        ):
            planner.replay_transcript_on_clean_transport(
                object_above_limit,
                transport_factory,
            )
        self.assertEqual(factory_calls, 0)
        with self.assertRaisesRegex(
            planner.PlannerError,
            "invalid planner transcript JSON",
        ):
            planner.PlannerTranscript.import_bytes(b'{"value":"\xff"}')

        bridge = planner.PlannerBridge(PROVENANCE)
        bridge.begin(PROVENANCE)
        observation = bridge.observe(
            1,
            (),
            (planner.Action("MOVE_WAIT", 1, (1, 0)),),
        )
        planner.collect_observation_pages(bridge, observation)
        valid = bridge.transcript.export()
        with mock.patch.object(
            planner.json,
            "loads",
            side_effect=RecursionError("parser recursion"),
        ):
            with self.assertRaisesRegex(
                planner.PlannerError,
                "invalid planner transcript JSON",
            ):
                planner.PlannerTranscript.import_bytes(valid)
        with mock.patch.object(
            planner.json,
            "dumps",
            side_effect=RecursionError("canonicalizer recursion"),
        ):
            with self.assertRaisesRegex(
                planner.PlannerError,
                "invalid planner transcript JSON recursion",
            ):
                planner.PlannerTranscript.import_bytes(valid)

    def test_completion_timing_is_typed_and_kind_bounded(self):
        bridge = planner.PlannerBridge(PROVENANCE)
        run_id = bridge.begin(PROVENANCE)
        observation = bridge.observe(
            1,
            (),
            tuple(
                planner.Action("MOVE_WAIT", 1, (index + 1, 0))
                for index in range(23)
            ),
        )
        complete = planner.collect_observation_pages(
            bridge,
            observation,
        )
        choice = complete.actions[0]
        bridge.commit(
            planner.Command(
                planner.CommandKind.COMMIT,
                run_id,
                observation.observation_id,
                choice.ordinal,
                choice.token,
            )
        )
        encoded = bridge.transcript.export()

        def mutate_completion(kind, response_frames):
            document = json.loads(encoded)
            completion = next(
                event
                for event in document["events"]
                if event["event"] == "completion"
                    and event["kind"] == kind
            )
            completion["response_frames"] = response_frames
            previous = "0" * 64
            for sequence, event in enumerate(document["events"]):
                event.pop("event_digest", None)
                event["sequence"] = sequence
                event["previous_digest"] = previous
                event["event_digest"] = planner._digest(event)
                previous = event["event_digest"]
            return planner._canonical(document)

        for kind, response_frames in (
            (4, planner.COMMAND_RESPONSE_FRAME_LIMIT),
            (2, planner.COMMAND_RESPONSE_FRAME_LIMIT + 1),
            (2, planner.COMMIT_COMPLETION_FRAME_LIMIT),
        ):
            with self.subTest(
                valid_kind=kind,
                response_frames=response_frames,
            ):
                imported = planner.PlannerTranscript.import_bytes(
                    mutate_completion(kind, response_frames)
                )
                self.assertTrue(imported.events)

        for kind, response_frames in (
            (4, -1),
            (4, planner.COMMAND_RESPONSE_FRAME_LIMIT + 1),
            (2, planner.COMMIT_COMPLETION_FRAME_LIMIT + 1),
            (4, True),
            (4, "1"),
            (4, 1.0),
        ):
            with self.subTest(
                invalid_kind=kind,
                response_frames=response_frames,
            ):
                with self.assertRaisesRegex(
                    planner.PlannerError,
                    "completion timing is invalid",
                ):
                    planner.PlannerTranscript.import_bytes(
                        mutate_completion(kind, response_frames)
                    )

        rejected_commit = json.loads(encoded)
        acknowledgement = next(
            event
            for event in rejected_commit["events"]
            if event["event"] == "acknowledgement"
                and event["kind"] == 2
        )
        acknowledgement["result"] = 0
        acknowledgement["rejection"] = 4
        completion = next(
            event
            for event in rejected_commit["events"]
            if event["event"] == "completion"
                and event["kind"] == 2
        )
        completion["response_frames"] = (
            planner.COMMAND_RESPONSE_FRAME_LIMIT + 1
        )
        previous = "0" * 64
        for sequence, event in enumerate(rejected_commit["events"]):
            event.pop("event_digest", None)
            event["sequence"] = sequence
            event["previous_digest"] = previous
            event["event_digest"] = planner._digest(event)
            previous = event["event_digest"]
        with self.assertRaisesRegex(
            planner.PlannerError,
            "completion timing is invalid",
        ):
            planner.PlannerTranscript.import_bytes(
                planner._canonical(rejected_commit)
            )

        factory_calls = 0

        def factory():
            nonlocal factory_calls
            factory_calls += 1
            raise AssertionError("invalid timing started transport")

        with self.assertRaisesRegex(
            planner.PlannerError,
            "completion timing is invalid",
        ):
            planner.replay_transcript_on_clean_transport(
                mutate_completion(4, -1),
                factory,
            )
        self.assertEqual(factory_calls, 0)

    def test_mailbox_has_no_arbitrary_memory_write_api(self):
        mailbox = planner.Mailbox()
        self.assertFalse(hasattr(mailbox, "write"))
        self.assertFalse(hasattr(mailbox, "address"))
        mailbox.submit(planner.Command(planner.CommandKind.START, 1, 0))
        with self.assertRaisesRegex(planner.PlannerError, "unconsumed"):
            mailbox.submit(planner.Command(planner.CommandKind.START, 1, 0))

    def test_maximum_semantic_transcript_fits_two_mib(self):
        available = planner.Availability.AVAILABLE
        map_cells = tuple(
            planner.MapCell(
                index % 64,
                index // 64,
                1,
                0,
                available,
            )
            for index in range(planner.MAX_MAP_CELLS)
        )
        units = tuple(
            planner.UnitRecord(
                index,
                1,
                1,
                (0, 0),
                (20, 20),
                0,
                0,
                available,
            )
            for index in range(planner.MAX_UNITS)
        )
        inventory = tuple(
            planner.InventoryRecord(
                index // planner.UNIT_ITEM_COUNT,
                index % planner.UNIT_ITEM_COUNT,
                1,
                30,
                0x1E01,
                available,
            )
            for index in range(
                planner.MAX_UNITS * planner.UNIT_ITEM_COUNT
            )
        )
        resources = (
            planner.ResourceRecord(
                planner.ValueKind.GOLD,
                None,
                999,
                None,
                None,
                available,
            ),
            *(
                planner.ResourceRecord(
                    planner.ValueKind.CONVOY_ITEM,
                    index,
                    0,
                    0,
                    0,
                    planner.Availability.EMPTY,
                )
                for index in range(planner.CONVOY_ITEM_COUNT)
            ),
            *(
                planner.ResourceRecord(
                    planner.ValueKind.AUTOPLAY_TELEMETRY,
                    index,
                    0,
                    None,
                    None,
                    available,
                )
                for index in range(planner.AUTOPLAY_TELEMETRY_WORDS)
            ),
        )
        flags = tuple(
            planner.FlagRecord(
                (
                    planner.ValueKind.PERMANENT_FLAG
                    if index < 2048
                    else planner.ValueKind.CHAPTER_FLAG
                ),
                index % 2048,
                index & 1,
                available,
            )
            for index in range(4096)
        )
        actions = tuple(
            planner.ActionRecord(
                index,
                planner.Action(
                    "MOVE_WAIT",
                    1,
                    (index % 64, index // 64),
                ),
                planner.OpaqueToken(
                    index,
                    index + 1,
                    index + 2,
                    index + 3,
                ),
            )
            for index in range(planner.MAX_ACTIONS)
        )
        components = (
            (planner.PageKind.MAP, "map_cells", map_cells, 224),
            (planner.PageKind.UNITS, "units", units, 56),
            (
                planner.PageKind.INVENTORY,
                "inventory",
                inventory,
                112,
            ),
            (
                planner.PageKind.RESOURCES,
                "resources",
                resources,
                112,
            ),
            (planner.PageKind.FLAGS, "flags", flags, 112),
            (
                planner.PageKind.ACTIONS,
                "actions",
                actions,
                planner.ACTIONS_PER_PAGE,
            ),
        )
        page_count = 1 + sum(
            (len(records) + capacity - 1) // capacity
            for _, _, records, capacity in components
        )
        self.assertEqual(page_count, 92)
        common = {
            "run_id": 1,
            "observation_id": 1,
            "chapter": 1,
            "fields": (),
            "actions": (),
            "page_count": page_count,
            "total_action_count": planner.MAX_ACTIONS,
        }
        pages = [planner.Observation(**common)]
        for page_kind, field, records, capacity in components:
            for start in range(0, len(records), capacity):
                pages.append(
                    planner.Observation(
                        **{
                            **common,
                            "page_index": len(pages),
                            "page_kind": page_kind,
                            field: records[start : start + capacity],
                        }
                    )
                )
        complete = planner.Observation(
            1,
            1,
            1,
            (),
            actions,
            page_count=page_count,
            total_action_count=planner.MAX_ACTIONS,
            map_cells=map_cells,
            units=units,
            inventory=inventory,
            resources=resources,
            flags=flags,
        )
        transcript = planner.PlannerTranscript()
        transcript.record_session(TRANSCRIPT_SESSION)
        largest_page_exchange = 0
        for page in pages:
            size_before = len(transcript.export())
            transcript.record_observation_page(page)
            transcript.record_settled(page, (0,) * 13, (0,) * 16)
            largest_page_exchange = max(
                largest_page_exchange,
                len(transcript.export()) - size_before,
            )
        self.assertLessEqual(
            largest_page_exchange,
            planner.MAX_TRANSCRIPT_EXCHANGE_BYTES,
        )
        transcript.record_complete_and_settled(
            complete,
            (0,) * 13,
            (0,) * 16,
        )
        exported = transcript.export()
        self.assertLessEqual(len(exported), planner.MAX_TRACE_BYTES)
        self.assertEqual(
            planner.PlannerTranscript.import_bytes(exported).export(),
            exported,
        )

    def test_action_page_decodes_actor_and_target_slots(self):
        words = [0] * 249
        words[:15] = [
            0x41504C4E,
            planner.PROTOCOL_VERSION,
            249 * 4,
            1,
            2,
            2,
            3,
            4,
            4,
            0,
            1,
            1,
            1,
            0,
            7,
        ]
        words[25:35] = [
            3,
            1,
            2 | (3 << 16),
            2 | (4 << 8) | (5 << 16),
            1 | (3 << 8),
            0x12345678,
            0x9ABCDEF0,
            0x0BADCAFE,
            0x10203040,
            5,
        ]
        observation = planner.parse_transport_observation(words)
        action = observation.actions[0].action
        self.assertEqual(action.item_slot, 1)
        self.assertEqual(action.target_item_slot, 3)
        self.assertEqual(action.target_position, (4, 5))

        self.assertEqual(
            observation.actions[0].token.words,
            (0x12345678, 0x9ABCDEF0, 0x0BADCAFE, 0x10203040),
        )

        words[29] = 0xFF | (0xFF << 8)
        observation = planner.parse_transport_observation(words)
        self.assertIsNone(observation.actions[0].action.item_slot)
        self.assertIsNone(observation.actions[0].action.target_item_slot)

        words[29] = 0 | (0xFF << 8)
        observation = planner.parse_transport_observation(words)
        self.assertEqual(observation.actions[0].action.item_slot, 0)
        with self.assertRaisesRegex(
            planner.PlannerError,
            "invalid optional item-slot sentinel",
        ):
            words[29] = planner.UNIT_ITEM_COUNT | (0xFF << 8)
            planner.parse_transport_observation(words)

    def test_page_identity_and_sequence_are_bounded_before_traversal(self):
        def summary_words():
            words = [0] * 249
            words[:15] = [
                0x41504C4E,
                planner.PROTOCOL_VERSION,
                249 * 4,
                1,
                2,
                2,
                0,
                1,
                1,
                0,
                planner.SEMANTIC_FIELD_COUNT,
                planner.SEMANTIC_FIELD_COUNT,
                1,
                0,
                7,
            ]
            for index in range(planner.SEMANTIC_FIELD_COUNT):
                words[25 + index * 2] = (
                    (4 << 24) | (index + 1)
                )
            return words

        for page_count, page_index in (
            (0, 0),
            (planner.MAX_PAGE_COUNT + 1, 0),
            (1_000_000_000, 0),
            (0xFFFFFFFF, 0),
            (1, 1),
        ):
            with self.subTest(
                page_count=page_count,
                page_index=page_index,
            ):
                words = summary_words()
                words[6] = page_index
                words[7] = page_count
                with self.assertRaisesRegex(
                    planner.PlannerError,
                    "page identity is outside v2 bounds",
                ):
                    planner.parse_transport_observation(words)
        for invalid_word in (-1, 0x100000000):
            words = summary_words()
            words[7] = invalid_word
            with self.assertRaisesRegex(
                planner.PlannerError,
                "outside fixed u32 range",
            ):
                planner.parse_transport_observation(words)

        class PageTransport:
            def __init__(self, pages):
                self.pages = pages
                self.exchange_count = 0

            def exchange(self, command):
                self.exchange_count += 1
                return self.pages[command.page_index]

        oversized = planner.Observation(
            1,
            1,
            1,
            (),
            (),
            page_count=planner.MAX_PAGE_COUNT + 1,
        )
        transport = PageTransport({})
        with self.assertRaisesRegex(
            planner.PlannerError,
            "traversal exceeds host bounds",
        ):
            planner.collect_observation_pages(transport, oversized)
        self.assertEqual(transport.exchange_count, 0)

        action = planner.ActionRecord(
            0,
            planner.Action("MOVE_WAIT", 1, (1, 0)),
            planner.OpaqueToken(1, 2, 3, 4),
        )
        first = planner.Observation(
            1,
            1,
            1,
            (),
            (),
            page_count=3,
        )
        out_of_order = PageTransport(
            {
                1: planner.Observation(
                    1,
                    1,
                    1,
                    (),
                    (action,),
                    page_index=1,
                    page_count=3,
                    page_kind=planner.PageKind.ACTIONS,
                    total_action_count=1,
                    record_count=1,
                    total_record_count=1,
                ),
                2: planner.Observation(
                    1,
                    1,
                    1,
                    (),
                    (),
                    page_index=2,
                    page_count=3,
                    page_kind=planner.PageKind.MAP,
                    total_action_count=1,
                    record_count=1,
                    total_record_count=1,
                    map_cells=(
                        planner.MapCell(
                            0,
                            0,
                            1,
                            0,
                            planner.Availability.AVAILABLE,
                        ),
                    ),
                ),
            }
        )
        with self.assertRaisesRegex(
            planner.PlannerError,
            "typed-page sequence is not canonical",
        ):
            planner.collect_observation_pages(out_of_order, first)
        self.assertEqual(out_of_order.exchange_count, 2)

        duplicate_index = PageTransport(
            {
                1: out_of_order.pages[1],
                2: out_of_order.pages[1],
            }
        )
        with self.assertRaisesRegex(
            planner.PlannerError,
            planner.Rejection.STALE_OBSERVATION.value,
        ):
            planner.collect_observation_pages(duplicate_index, first)
        self.assertEqual(duplicate_index.exchange_count, 2)

        missing_span = PageTransport(
            {
                1: replace(
                    out_of_order.pages[1],
                    page_count=3,
                    record_start=0,
                    total_record_count=2,
                ),
                2: replace(
                    out_of_order.pages[1],
                    page_index=2,
                    page_count=3,
                    record_start=2,
                    total_record_count=3,
                ),
            }
        )
        with self.assertRaisesRegex(
            planner.PlannerError,
            "record span is not canonical",
        ):
            planner.collect_observation_pages(missing_span, first)
        self.assertEqual(missing_span.exchange_count, 2)

        production_first = replace(
            first,
            page_count=2,
            actual_rom_identity=1,
        )
        incomplete = PageTransport(
            {
                1: replace(
                    out_of_order.pages[1],
                    page_count=2,
                ),
            }
        )
        with self.assertRaisesRegex(
            planner.PlannerError,
            "typed-page sequence is not canonical",
        ):
            planner.collect_observation_pages(
                incomplete,
                production_first,
            )

    def test_host_begin_and_commit_limits_are_atomic(self):
        boundary = planner.PlannerBridge(PROVENANCE)
        boundary.begin(PROVENANCE)
        boundary_observation = boundary.observe(
            1,
            (),
            (planner.Action("MOVE_WAIT", 1, (1, 1)),),
        )
        boundary_complete = planner.collect_observation_pages(
            boundary, boundary_observation
        )
        boundary._committed_count = planner.MAX_TRACE_ACTIONS - 1
        boundary.commit(
            planner.Command(
                planner.CommandKind.COMMIT,
                boundary_observation.run_id,
                boundary_observation.observation_id,
                0,
                boundary_complete.actions[0].token,
            )
        )
        self.assertEqual(boundary._committed_count, planner.MAX_TRACE_ACTIONS)

        bridge = planner.PlannerBridge(PROVENANCE)
        bridge.begin(PROVENANCE)
        with self.assertRaisesRegex(
            planner.PlannerError,
            planner.Rejection.PROTOCOL_ERROR.value,
        ):
            bridge.begin(PROVENANCE)

        observation = bridge.observe(
            1,
            (),
            (planner.Action("MOVE_WAIT", 1, (1, 1)),),
        )
        complete = planner.collect_observation_pages(bridge, observation)
        command = planner.Command(
            planner.CommandKind.COMMIT,
            observation.run_id,
            observation.observation_id,
            0,
            complete.actions[0].token,
        )
        bridge._committed_count = planner.MAX_TRACE_ACTIONS
        trace_before = bridge.transcript.export()
        with self.assertRaisesRegex(
            planner.PlannerError,
            planner.Rejection.RESOURCE_LIMIT.value,
        ):
            bridge.commit(command)
        self.assertEqual(bridge.transcript.export(), trace_before)
        self.assertIs(bridge._observation, observation)
        with self.assertRaisesRegex(
            planner.PlannerError,
            planner.Rejection.CANCELLED.value,
        ):
            bridge.commit(
                planner.Command(
                    planner.CommandKind.CANCEL,
                    observation.run_id,
                    observation.observation_id,
                )
            )
        bridge.begin(PROVENANCE)

        oversized = planner.PlannerBridge(PROVENANCE)
        oversized.begin(PROVENANCE)
        observation = oversized.observe(
            1,
            (),
            (planner.Action("MOVE_WAIT", 1, (1, 1)),),
        )
        complete = planner.collect_observation_pages(oversized, observation)
        command = planner.Command(
            planner.CommandKind.COMMIT,
            observation.run_id,
            observation.observation_id,
            0,
            complete.actions[0].token,
        )
        oversized.transcript = planner.PlannerTranscript(
            max_bytes=planner.MAX_TRANSCRIPT_EXCHANGE_BYTES
        )
        trace_before = oversized.transcript.export()
        with self.assertRaisesRegex(
            planner.PlannerError,
            planner.Rejection.RESOURCE_LIMIT.value,
        ):
            oversized.commit(command)
        self.assertEqual(oversized.transcript.export(), trace_before)
        self.assertIs(oversized._observation, observation)

        atomic = planner.PlannerBridge(PROVENANCE)
        atomic.begin(PROVENANCE)
        atomic.transcript = planner.PlannerTranscript(max_bytes=256)
        trace_before = atomic.transcript.export()
        next_id_before = atomic._next_observation_id
        with self.assertRaisesRegex(
            planner.PlannerError,
            planner.Rejection.RESOURCE_LIMIT.value,
        ):
            atomic.observe(
                1,
                (),
                (planner.Action("MOVE_WAIT", 1, (1, 1)),),
            )
        self.assertEqual(atomic.transcript.export(), trace_before)
        self.assertEqual(atomic._next_observation_id, next_id_before)
        self.assertIsNone(atomic._observation)

    def test_security_boundary_has_no_raw_memory_save_or_network_surface(self):
        root = TESTS_DIR.parents[2]
        target = (root / "src" / "expansion_autoplay_planner.c").read_text(encoding="utf-8")
        host = (PLAYTEST_DIR / "autoplay_planner.py").read_text(encoding="utf-8")
        transport = (PLAYTEST_DIR / "planner_transport_backend.c").read_text(
            encoding="utf-8"
        )
        self.assertEqual(
            set(re.findall(r"gActionData\.([A-Za-z0-9_]+)\s*=", target)),
            {"xOther", "yOther", "itemSlotIndex", "trapType"},
        )
        self.assertNotIn("busWrite", target)
        self.assertNotIn("socket", host)
        self.assertNotIn("subprocess", host)
        self.assertNotIn("savestate", host)
        self.assertNotIn("ADDRESS", transport)
        self.assertNotIn("POKE", transport)
        self.assertNotIn('"STEP"', transport)
        self.assertNotIn('"RUN"', transport)
        self.assertNotIn("setKeys", transport)
        self.assertIn("PLANNER_COMMAND_ADDR", transport)

    def test_cp_decide_wait_uses_dedicated_mailbox_poll_state(self):
        root = TESTS_DIR.parents[2]
        source = (root / "src" / "cp_decide.c").read_text(encoding="utf-8")
        wait_case = source.split(
            "case EXPANSION_AUTOPLAY_PLANNER_DECISION_WAIT:", 1
        )[1].split("case EXPANSION_AUTOPLAY_PLANNER_DECISION_CANCELLED:", 1)[0]
        self.assertIn("Proc_Goto(proc, 1)", wait_case)
        self.assertNotIn("Proc_Goto(proc, 0)", wait_case)
        poll_state = source.split("PROC_LABEL(1)", 1)[1].split(
            "PROC_LABEL(2)", 1
        )[0]
        self.assertIn("PROC_REPEAT(CpDecide_PollPlanner)", poll_state)
        poll_function = source.split(
            "static void CpDecide_PollPlanner(ProcPtr proc)", 2
        )[-1].split("static void CpDecide_CompleteDecision", 1)[0]
        self.assertIn("ExpansionAutoplayPlanner_PollDecision", poll_function)
        self.assertNotIn("AiDecideMainFunc", poll_function)
        self.assertNotIn("AiClearDecision", poll_function)
        planner_branch = source.split(
            "if (ExpansionAutoplayPlanner_IsActive())", 1
        )[1].split("else", 1)[0]
        self.assertIn("AiGenerateUnitMovementMapRespectStay", planner_branch)
        self.assertIn("ExpansionAutoplayPlanner_OfferDecision(NULL)", planner_branch)
        self.assertNotIn("AiDecideMainFunc", planner_branch)

    def test_map_lifecycle_preserves_only_true_chapter_transition(self):
        root = TESTS_DIR.parents[2]
        source = (root / "src" / "bmio.c").read_text(encoding="utf-8")
        event = (root / "src" / "event.c").read_text(encoding="utf-8")
        event_commands = (root / "src" / "eventscr.c").read_text(encoding="utf-8")
        autoplay = (root / "src" / "expansion_autoplay.c").read_text(
            encoding="utf-8"
        )
        start = source.split("void StartBattleMap", 1)[1].split(
            "void RestartBattleMap", 1
        )[0]
        restart = source.split("void RestartBattleMap", 1)[1].split(
            "void GameCtrl_StartResumedGame", 1
        )[0]
        resume = source.split("void GameCtrl_StartResumedGame", 1)[1].split(
            "void EndBMapMain", 1
        )[0]
        teardown = source.split("static void EndBMapMainInternal", 1)[1].split(
            "void ChapterChangeUnitCleanup", 1
        )[0]
        self.assertIn("ExpansionAutoplay_ResetForChapterTransition()", start)
        self.assertIn("ExpansionAutoplayPlanner_OnMapReady()", start)
        for destructive in (restart, resume):
            self.assertIn("ExpansionAutoplay_Reset()", destructive)
            self.assertNotIn(
                "ExpansionAutoplay_ResetForChapterTransition()",
                destructive,
            )
            self.assertIn("ExpansionAutoplayPlanner_OnMapReady()", destructive)
        player_phase = autoplay.split(
            "void ExpansionAutoplay_OnPlayerPhaseStart", 1
        )[1].split("void ExpansionAutoplay_OnBlueComputerPhaseStart", 1)[0]
        self.assertIn("ExpansionAutoplayPlanner_OnMapReady()", player_phase)
        self.assertIn("EndBMapMainInternal(false)", teardown)
        self.assertIn("EndBMapMainInternal(true)", teardown)
        self.assertIn("ExpansionAutoplay_ResetForChapterTransition()", teardown)
        self.assertIn("ExpansionAutoplay_Reset()", teardown)
        self.assertIn(
            "EV_STATE_PLANNER_CHAPTER_TRANSITION",
            event,
        )
        preserving_end = event.split(
            "if (proc->evStateBits & "
            "EV_STATE_PLANNER_CHAPTER_TRANSITION)",
            1,
        )[1].split("else", 1)[0]
        self.assertLess(
            preserving_end.index(
                "ExpansionAutoplayPlanner_RecordCampaignCheckpoint()"
            ),
            preserving_end.index("EndBMapMainForChapterTransition()"),
        )
        mnch = event_commands.split("case EVSUBCMD_MNCH:", 1)[1].split(
            "case EVSUBCMD_MNC2:", 1
        )[0]
        mnc2 = event_commands.split("case EVSUBCMD_MNC2:", 1)[1].split(
            "case EVSUBCMD_MNC3:", 1
        )[0]
        mnts = event_commands.split("case EVSUBCMD_MNTS:", 1)[1].split(
            "case EVSUBCMD_MNCH:", 1
        )[0]
        mnc4 = event_commands.split("case EVSUBCMD_MNC4:", 1)[1].split(
            "} // switch", 1
        )[0]
        for preserving in (mnch, mnc2):
            self.assertIn("EV_STATE_PLANNER_CHAPTER_TRANSITION", preserving)
        for destructive in (mnts, mnc4):
            self.assertNotIn("EV_STATE_PLANNER_CHAPTER_TRANSITION", destructive)

    def test_debug_only_configuration_rejects_release_mailbox(self):
        root = TESTS_DIR.parents[2]
        command = [
            "python3",
            "scripts/modernize/expansion_config.py",
            "validate",
            "--config-mk",
            "config.mk",
            "--abi",
            "aapcs",
            "--rom-size",
            "16M",
            "--text-shift",
            "0",
            "--repo-root",
            ".",
            "--autoplay-planner",
            "1",
        ]
        debug = subprocess.run(
            [*command, "--config", "debug"], cwd=root, capture_output=True, text=True
        )
        self.assertEqual(debug.returncode, 0, debug.stdout + debug.stderr)
        release = subprocess.run(
            [*command, "--config", "release"], cwd=root, capture_output=True, text=True
        )
        self.assertNotEqual(release.returncode, 0)
        self.assertIn("modern-debug-only", release.stderr)

    def test_configured_bare_make_selects_release_and_fails_closed(self):
        root = TESTS_DIR.parents[2]
        build_root = root / "build" / "test-artifacts" / "planner-configure"
        build_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            real_make = shutil.which("make")
            self.assertIsNotNone(real_make, "GNU Make is required")
            forbidden_compiler = Path(temporary) / "arm-none-eabi-gcc"
            forbidden_compiler_record = (
                Path(temporary) / "arm-compiler-was-invoked"
            )
            forbidden_compiler.write_text(
                """#!/bin/sh
: > "$PLANNER_FORBIDDEN_COMPILER_RECORD"
exit 97
""",
                encoding="utf-8",
            )
            forbidden_compiler.chmod(0o755)
            environment = os.environ.copy()
            environment.pop("MAKEFLAGS", None)
            environment["PATH"] = (
                f"{temporary}{os.pathsep}{environment['PATH']}"
            )
            environment["PLANNER_FORBIDDEN_COMPILER_RECORD"] = str(
                forbidden_compiler_record
            )
            configured = subprocess.run(
                [str(root / "configure"), "--enable-autoplay-planner"],
                cwd=temporary,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(
                configured.returncode,
                0,
                configured.stdout + configured.stderr,
            )
            fragment = (Path(temporary) / "config.autotools.mk").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("MODERN_CONFIG :=", fragment)
            self.assertIn("EXPANSION_AUTOPLAY_PLANNER := 1", fragment)

            recorder = Path(temporary) / "recursive-make-recorder"
            record_path = Path(temporary) / "recursive-make.json"
            recorder.write_text(
                """#!/usr/bin/env python3
import json
import os
import subprocess
import sys

arguments = sys.argv[1:]
goal = arguments[-1]
child = subprocess.run(
    [os.environ["PLANNER_REAL_MAKE"], *arguments],
    capture_output=True,
    text=True,
)
with open(os.environ["PLANNER_MAKE_RECORD"], "w", encoding="utf-8") as output:
    json.dump(
        {
            "arguments": arguments,
            "goal": goal,
            "child_returncode": child.returncode,
            "child_stdout": child.stdout,
            "child_stderr": child.stderr,
        },
        output,
        sort_keys=True,
    )
sys.stdout.write(child.stdout)
sys.stderr.write(child.stderr)
raise SystemExit(child.returncode)
""",
                encoding="utf-8",
            )
            recorder.chmod(0o755)
            environment["PLANNER_REAL_MAKE"] = real_make
            environment["PLANNER_MAKE_RECORD"] = str(record_path)
            bare_make = subprocess.run(
                [
                    real_make,
                    "--no-print-directory",
                    f"MAKE={recorder}",
                ],
                cwd=temporary,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertNotEqual(bare_make.returncode, 0)
            record = json.loads(record_path.read_text(encoding="utf-8"))
            self.assertEqual(record["goal"], "all")
            self.assertNotEqual(record["child_returncode"], 0)
            self.assertIn(
                "modern-debug-only",
                record["child_stdout"] + record["child_stderr"],
            )
            self.assertIn(
                "make expansion-modern-boot-check MODERN_CONFIG=debug",
                record["child_stdout"] + record["child_stderr"],
            )
            release = subprocess.run(
                [
                    real_make,
                    "--no-print-directory",
                    "expansion-modern-rom",
                    "MODERN_CONFIG=release",
                ],
                cwd=temporary,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertNotEqual(release.returncode, 0)
            self.assertIn("modern-debug-only", release.stdout + release.stderr)
            self.assertFalse(
                forbidden_compiler_record.exists(),
                "host-only configure coverage invoked the ARM compiler",
            )

    def test_configured_explicit_debug_goal_builds_in_toolchain_lane(self):
        if host_mode.host_only_enabled():
            self.skipTest("configured ROM build belongs to the toolchain lane")
        if shutil.which("arm-none-eabi-gcc") is None:
            self.skipTest("ARM compiler unavailable")
        root = TESTS_DIR.parents[2]
        build_root = (
            root / "build" / "test-artifacts" / "planner-configure-toolchain"
        )
        build_root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            environment = os.environ.copy()
            environment.pop("MAKEFLAGS", None)
            configured = subprocess.run(
                [str(root / "configure"), "--enable-autoplay-planner"],
                cwd=temporary,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(
                configured.returncode,
                0,
                configured.stdout + configured.stderr,
            )
            fragment = (Path(temporary) / "config.autotools.mk").read_text(
                encoding="utf-8"
            )
            self.assertNotIn("MODERN_CONFIG :=", fragment)
            self.assertIn("EXPANSION_AUTOPLAY_PLANNER := 1", fragment)
            built = subprocess.run(
                [
                    "make",
                    "--no-print-directory",
                    "expansion-modern-boot-check",
                    "MODERN_CONFIG=debug",
                ],
                cwd=temporary,
                capture_output=True,
                text=True,
                env=environment,
            )
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)

    def test_public_protocol_layout_is_fixed_width_and_offset_stable(self):
        compiler = shutil.which("gcc") or shutil.which("cc")
        if compiler is None:
            self.skipTest("no host C compiler")
        root = TESTS_DIR.parents[2]
        build_root = root / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            executable = Path(temporary) / "planner-layout-driver"
            completed = subprocess.run(
                [
                    compiler,
                    "-std=gnu89",
                    "-Werror=declaration-after-statement",
                    "-Werror=implicit-function-declaration",
                    "-Werror=implicit-int",
                    "-I",
                    str(root / "include"),
                    "-I",
                    str(root / "include" / "generated"),
                    str(
                        TESTS_DIR
                        / "c"
                        / "expansion_autoplay_planner_layout_driver.c"
                    ),
                    "-o",
                    str(executable),
                ],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stdout + completed.stderr,
            )
            completed = subprocess.run(
                [str(executable)],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stdout + completed.stderr,
            )
            layout = {
                key: int(value)
                for key, value in (
                    line.split("=", 1)
                    for line in completed.stdout.splitlines()
                )
            }
            self.assertEqual(
                layout,
                {
                    "semantic_size": 8,
                    "action_size": 40,
                    "unit_size": 16,
                    "value_size": 8,
                    "start_union_size": 4,
                    "count_union_size": 4,
                    "payload_union_size": 896,
                    "observation_size": 996,
                    "observation_start_offset": 36,
                    "observation_count_offset": 40,
                    "observation_payload_offset": 100,
                    "command_size": 64,
                    "command_payload_offset": 32,
                    "command_result_offset": 56,
                    "checkpoint_size": 52,
                    "checkpoint_mode_offset": 20,
                },
            )

    def test_transport_acknowledgement_enum_is_exact(self):
        root = TESTS_DIR.parents[2]
        build_root = root / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            executable = Path(temporary) / "planner-transport-ack"
            try:
                build_planner_transport_ack_driver(executable)
            except RuntimeError as error:
                if (
                    "planner transport host compiler unavailable"
                        in str(error)
                    or "mgba" in str(error).lower()
                ):
                    self.skipTest(str(error))
                raise
            completed = subprocess.run(
                [str(executable)],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stdout + completed.stderr,
            )
            self.assertIn(
                "PLANNER_TRANSPORT_ACK_TEST: PASS",
                completed.stdout,
            )

    def test_c_mailbox_adapter_accepts_only_typed_token_commit(self):
        compiler = shutil.which("gcc") or shutil.which("cc")
        if compiler is None:
            self.skipTest("no host C compiler")
        root = TESTS_DIR.parents[2]
        build_root = root / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            executable = Path(temporary) / "planner-driver"
            completed = subprocess.run(
                [
                    compiler,
                    "-std=gnu89",
                    "-Werror=declaration-after-statement",
                    "-Werror=implicit-function-declaration",
                    "-Werror=implicit-int",
                    "-O2",
                    "-ffunction-sections",
                    "-fdata-sections",
                    "-I",
                    str(root / "include"),
                    "-I",
                    str(root / "include" / "generated"),
                    "-DFE8_EXPANSION_MODERN_BUILD=1",
                    "-DFE8_EXPANSION_DEBUG=1",
                    "-DFE8_EXPANSION_AUTOPLAY_PLANNER=1",
                    "-DFE8_AUTOPLAY_PLANNER_RUNTIME_TEST=1",
                    str(root / "src" / "action_semantics.c"),
                    str(root / "src" / "bmtarget.c"),
                    str(root / "src" / "expansion_autoplay_planner.c"),
                    str(TESTS_DIR / "c" / "expansion_autoplay_planner_driver.c"),
                    "-Wl,--gc-sections",
                    "-o",
                    str(executable),
                ],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            completed = subprocess.run(
                [str(executable)], cwd=root, capture_output=True, text=True
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            self.assertIn("AUTOPLAY_PLANNER_HOST_TEST: PASS", completed.stdout)

    def test_native_action_semantics_execute_selected_fields(self):
        compiler = shutil.which("gcc") or shutil.which("cc")
        if compiler is None:
            self.skipTest("no host C compiler")
        root = TESTS_DIR.parents[2]
        build_root = root / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            executable = Path(temporary) / "action-semantics-driver"
            completed = subprocess.run(
                [
                    compiler,
                    "-std=gnu89",
                    "-Werror=declaration-after-statement",
                    "-Werror=implicit-function-declaration",
                    "-Werror=implicit-int",
                    "-O2",
                    "-I",
                    str(root / "include"),
                    "-I",
                    str(root / "include" / "generated"),
                    "-DFE8_EXPANSION_MODERN_BUILD=1",
                    "-DFE8_EXPANSION_DEBUG=1",
                    "-DFE8_EXPANSION_AUTOPLAY_PLANNER=1",
                    str(root / "src" / "action_semantics.c"),
                    str(TESTS_DIR / "c" / "action_semantics_driver.c"),
                    "-o",
                    str(executable),
                ],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stdout + completed.stderr,
            )
            completed = subprocess.run(
                [str(executable)],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stdout + completed.stderr,
            )
            self.assertIn("ACTION_SEMANTICS_HOST_TEST: PASS", completed.stdout)

    def test_native_snag_combat_executes_selected_obstacle(self):
        compiler = shutil.which("gcc") or shutil.which("cc")
        if compiler is None:
            self.skipTest("no host C compiler")
        root = TESTS_DIR.parents[2]
        build_root = root / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            executable = Path(temporary) / "planner-snag-executor"
            environment = os.environ.copy()
            environment["TMPDIR"] = temporary
            completed = subprocess.run(
                [
                    compiler,
                    "-std=gnu89",
                    "-Werror=declaration-after-statement",
                    "-Werror=implicit-function-declaration",
                    "-Werror=implicit-int",
                    "-O2",
                    "-ffunction-sections",
                    "-fdata-sections",
                    "-I",
                    str(root / "include"),
                    "-I",
                    str(root / "include" / "generated"),
                    "-DFE8_EXPANSION_MODERN_BUILD=1",
                    "-DFE8_EXPANSION_DEBUG=1",
                    "-DFE8_EXPANSION_AUTOPLAY_PLANNER=1",
                    str(root / "src" / "cp_perform.c"),
                    str(root / "src" / "bmbattle.c"),
                    str(
                        TESTS_DIR
                        / "c"
                        / "planner_snag_executor_driver.c"
                    ),
                    "-Wl,--gc-sections",
                    "-o",
                    str(executable),
                ],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stdout + completed.stderr,
            )
            completed = subprocess.run(
                [str(executable)],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stdout + completed.stderr,
            )
            self.assertIn(
                "PLANNER_SNAG_EXECUTOR_TEST: PASS",
                completed.stdout,
            )

    def test_real_target_builder_excludes_not_deployed_units(self):
        compiler = shutil.which("gcc") or shutil.which("cc")
        if compiler is None:
            self.skipTest("no host C compiler")
        root = TESTS_DIR.parents[2]
        build_root = root / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            executable = Path(temporary) / "planner-target-availability"
            completed = subprocess.run(
                [
                    compiler,
                    "-std=gnu89",
                    "-Werror=declaration-after-statement",
                    "-Werror=implicit-function-declaration",
                    "-Werror=implicit-int",
                    "-O2",
                    "-ffunction-sections",
                    "-fdata-sections",
                    "-I",
                    str(root / "include"),
                    "-I",
                    str(root / "include" / "generated"),
                    str(root / "src" / "bmtarget.c"),
                    str(
                        TESTS_DIR
                        / "c"
                        / "planner_target_availability_driver.c"
                    ),
                    "-Wl,--gc-sections",
                    "-o",
                    str(executable),
                ],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stdout + completed.stderr,
            )
            completed = subprocess.run(
                [str(executable)],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stdout + completed.stderr,
            )
            self.assertIn(
                "PLANNER_TARGET_AVAILABILITY_TEST: PASS",
                completed.stdout,
            )

    def test_native_summon_executor_preserves_action_and_coordinates(self):
        compiler = shutil.which("gcc") or shutil.which("cc")
        if compiler is None:
            self.skipTest("no host C compiler")
        root = TESTS_DIR.parents[2]
        build_root = root / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            executable = Path(temporary) / "summon-executor-driver"
            completed = subprocess.run(
                [
                    compiler,
                    "-std=gnu89",
                    "-Werror=declaration-after-statement",
                    "-Werror=implicit-function-declaration",
                    "-Werror=implicit-int",
                    "-O2",
                    "-ffunction-sections",
                    "-fdata-sections",
                    "-I",
                    str(root / "include"),
                    "-I",
                    str(root / "include" / "generated"),
                    "-DFE8_EXPANSION_MODERN_BUILD=1",
                    "-DFE8_EXPANSION_DEBUG=1",
                    "-DFE8_EXPANSION_AUTOPLAY_PLANNER=1",
                    str(root / "src" / "cp_perform.c"),
                    str(TESTS_DIR / "c" / "summon_executor_driver.c"),
                    "-Wl,--gc-sections",
                    "-o",
                    str(executable),
                ],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stdout + completed.stderr,
            )
            completed = subprocess.run(
                [str(executable)],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stdout + completed.stderr,
            )
            self.assertIn("SUMMON_EXECUTOR_HOST_TEST: PASS", completed.stdout)

    def test_native_summon_effect_uses_selected_coordinates(self):
        compiler = shutil.which("gcc") or shutil.which("cc")
        if compiler is None:
            self.skipTest("no host C compiler")
        root = TESTS_DIR.parents[2]
        build_root = root / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            executable = Path(temporary) / "summon-effect-driver"
            completed = subprocess.run(
                [
                    compiler,
                    "-std=gnu89",
                    "-Werror=declaration-after-statement",
                    "-Werror=implicit-function-declaration",
                    "-Werror=implicit-int",
                    "-O2",
                    "-ffunction-sections",
                    "-fdata-sections",
                    "-I",
                    str(root / "include"),
                    "-I",
                    str(root / "include" / "generated"),
                    "-DFE8_EXPANSION_MODERN_BUILD=1",
                    str(root / "src" / "mapanim_summon.c"),
                    str(TESTS_DIR / "c" / "summon_effect_driver.c"),
                    "-Wl,--gc-sections",
                    "-o",
                    str(executable),
                ],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stdout + completed.stderr,
            )
            completed = subprocess.run(
                [str(executable)],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stdout + completed.stderr,
            )
            self.assertIn("SUMMON_EFFECT_HOST_TEST: PASS", completed.stdout)

    def test_arm_adapter_compiles_at_the_existing_computer_decision_boundary(self):
        compiler = shutil.which("arm-none-eabi-gcc")
        nm = shutil.which("arm-none-eabi-nm")
        size = shutil.which("arm-none-eabi-size")
        if compiler is None or nm is None or size is None:
            self.skipTest("ARM compiler/binutils unavailable")
        root = TESTS_DIR.parents[2]
        build_root = root / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            temporary_path = Path(temporary)
            objects = []
            for source in (
                root / "src" / "expansion_autoplay_planner.c",
                root / "src" / "action_semantics.c",
                root / "src" / "cp_decide.c",
            ):
                output = temporary_path / f"{source.stem}.o"
                completed = subprocess.run(
                    [
                        compiler,
                        "-mcpu=arm7tdmi",
                        "-mthumb",
                        "-mthumb-interwork",
                        "-mabi=aapcs",
                        "-std=gnu89",
                        "-ffreestanding",
                        "-fno-builtin",
                        "-O2",
                        "-Werror=declaration-after-statement",
                        "-Werror=implicit-function-declaration",
                        "-Werror=implicit-int",
                        "-I",
                        str(root / "include"),
                        "-I",
                        str(root / "include" / "generated"),
                        "-DFE8_EXPANSION_MODERN_BUILD=1",
                        "-DFE8_EXPANSION_DEBUG=1",
                        "-DFE8_EXPANSION_AUTOPLAY_PLANNER=1",
                        "-c",
                        str(source),
                        "-o",
                        str(output),
                    ],
                    cwd=root,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
                objects.append(output)
            symbols = subprocess.run(
                [nm, "-S", *map(str, objects)], cwd=root, capture_output=True, text=True
            )
            self.assertEqual(symbols.returncode, 0, symbols.stdout + symbols.stderr)
            self.assertRegex(
                symbols.stdout,
                r"\bU ExpansionAutoplayPlanner_OfferDecision\b",
            )
            observation = re.search(
                r"^[0-9a-fA-F]+\s+([0-9a-fA-F]+)\s+[bBdD]\s+"
                r"gExpansionAutoplayPlannerObservation$",
                symbols.stdout,
                re.MULTILINE,
            )
            self.assertIsNotNone(observation, "planner observation symbol missing")
            self.assertEqual(int(observation.group(1), 16), 996)
            self.assertLessEqual(int(observation.group(1), 16), planner.PAGE_MAX_BYTES)
            command = re.search(
                r"^[0-9a-fA-F]+\s+([0-9a-fA-F]+)\s+[bBdD]\s+"
                r"gExpansionAutoplayPlannerCommand$",
                symbols.stdout,
                re.MULTILINE,
            )
            checkpoint = re.search(
                r"^[0-9a-fA-F]+\s+([0-9a-fA-F]+)\s+[bBdD]\s+"
                r"gExpansionAutoplayPlannerCampaignCheckpoint$",
                symbols.stdout,
                re.MULTILINE,
            )
            self.assertIsNotNone(command, "planner command symbol missing")
            self.assertIsNotNone(checkpoint, "planner checkpoint symbol missing")
            self.assertEqual(int(command.group(1), 16), 64)
            self.assertEqual(int(checkpoint.group(1), 16), 52)
            self.assertNotIn("sPlannerCandidates", symbols.stdout)
            self.assertNotIn("sPlannerSelectedDecision", symbols.stdout)

            sections = subprocess.run(
                [size, "-A", str(objects[0]), str(objects[1])],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(sections.returncode, 0, sections.stdout + sections.stderr)
            section_sizes: dict[str, int] = {}
            for section, value in re.findall(
                r"^(\S+)\s+(\d+)\s+\d+$",
                sections.stdout,
                re.MULTILINE,
            ):
                section_sizes[section] = (
                    section_sizes.get(section, 0) + int(value)
                )
            self.assertLessEqual(
                section_sizes.get("ewram_data", 0)
                + section_sizes.get(".bss", 0),
                4096,
            )
            self.assertEqual(section_sizes.get("iwram_data", 0), 0)
            planner_code_size = (
                section_sizes[".text"]
                + section_sizes[".rodata"]
                + section_sizes.get(".rodata.str1.4", 0)
            )
            self.assertLessEqual(
                planner_code_size,
                12 * 1024,
            )

            hook_code_sizes: dict[bool, int] = {}
            hook_objects: dict[bool, Path] = {}
            for enabled in (False, True):
                output = temporary_path / f"cp-perform-planner-{int(enabled)}.o"
                completed = subprocess.run(
                    [
                        compiler,
                        "-mcpu=arm7tdmi",
                        "-mthumb",
                        "-mthumb-interwork",
                        "-mabi=aapcs",
                        "-std=gnu89",
                        "-ffreestanding",
                        "-fno-builtin",
                        "-O2",
                        "-I",
                        str(root / "include"),
                        "-I",
                        str(root / "include" / "generated"),
                        "-DFE8_EXPANSION_MODERN_BUILD=1",
                        "-DFE8_EXPANSION_DEBUG=1",
                        f"-DFE8_EXPANSION_AUTOPLAY_PLANNER={int(enabled)}",
                        "-c",
                        str(root / "src" / "cp_perform.c"),
                        "-o",
                        str(output),
                    ],
                    cwd=root,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    completed.stdout + completed.stderr,
                )
                hook_objects[enabled] = output
                sizes = subprocess.run(
                    [size, "-A", str(output)],
                    cwd=root,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(
                    sizes.returncode,
                    0,
                    sizes.stdout + sizes.stderr,
                )
                hook_sections = {
                    section: int(value)
                    for section, value in re.findall(
                        r"^(\S+)\s+(\d+)\s+\d+$",
                        sizes.stdout,
                        re.MULTILINE,
                    )
                }
                hook_code_sizes[enabled] = (
                    hook_sections.get(".text", 0)
                    + hook_sections.get(".rodata", 0)
                    + hook_sections.get(".rodata.str1.4", 0)
                )
            hook_code_delta = (
                hook_code_sizes[True] - hook_code_sizes[False]
            )
            self.assertGreaterEqual(hook_code_delta, 0)
            self.assertLessEqual(
                planner_code_size + hook_code_delta,
                12 * 1024,
            )
            transition_code_sizes: dict[bool, int] = {}
            for enabled in (False, True):
                output = temporary_path / (
                    f"event-planner-{int(enabled)}.o"
                )
                completed = subprocess.run(
                    [
                        compiler,
                        "-mcpu=arm7tdmi",
                        "-mthumb",
                        "-mthumb-interwork",
                        "-mabi=aapcs",
                        "-std=gnu89",
                        "-ffreestanding",
                        "-fno-builtin",
                        "-O2",
                        "-I",
                        str(root / "include"),
                        "-I",
                        str(root / "include" / "generated"),
                        "-DFE8_EXPANSION_MODERN_BUILD=1",
                        "-DFE8_EXPANSION_DEBUG=1",
                        f"-DFE8_EXPANSION_AUTOPLAY_PLANNER={int(enabled)}",
                        "-c",
                        str(root / "src" / "event.c"),
                        "-o",
                        str(output),
                    ],
                    cwd=root,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(
                    completed.returncode,
                    0,
                    completed.stdout + completed.stderr,
                )
                sizes = subprocess.run(
                    [size, "-A", str(output)],
                    cwd=root,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(
                    sizes.returncode,
                    0,
                    sizes.stdout + sizes.stderr,
                )
                transition_code_sizes[enabled] = sum(
                    int(value)
                    for section, value in re.findall(
                        r"^(\S+)\s+(\d+)\s+\d+$",
                        sizes.stdout,
                        re.MULTILINE,
                    )
                    if section
                        in {".text", ".rodata", ".rodata.str1.4"}
                )
            transition_code_delta = (
                transition_code_sizes[True]
                - transition_code_sizes[False]
            )
            self.assertGreaterEqual(transition_code_delta, 0)
            self.assertLessEqual(
                planner_code_size
                + hook_code_delta
                + transition_code_delta,
                12 * 1024,
            )
            disabled_hook_symbols = subprocess.run(
                [nm, str(hook_objects[False])],
                cwd=root,
                capture_output=True,
                text=True,
            )
            enabled_hook_symbols = subprocess.run(
                [nm, str(hook_objects[True])],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                disabled_hook_symbols.returncode,
                0,
                disabled_hook_symbols.stdout + disabled_hook_symbols.stderr,
            )
            self.assertEqual(
                enabled_hook_symbols.returncode,
                0,
                enabled_hook_symbols.stdout + enabled_hook_symbols.stderr,
            )
            self.assertNotIn("AiSummonAction", disabled_hook_symbols.stdout)
            self.assertIn("AiSummonAction", enabled_hook_symbols.stdout)

            profile_sections: dict[bool, dict[str, int]] = {}
            for enabled in (False, True):
                profile_objects = []
                for source in (
                    root / "src" / "expansion_autoplay.c",
                    root / "src" / "rng.c",
                ):
                    output = temporary_path / (
                        f"{source.stem}-planner-{int(enabled)}.o"
                    )
                    completed = subprocess.run(
                        [
                            compiler,
                            "-mcpu=arm7tdmi",
                            "-mthumb",
                            "-mthumb-interwork",
                            "-mabi=aapcs",
                            "-std=gnu89",
                            "-ffreestanding",
                            "-fno-builtin",
                            "-O2",
                            "-I",
                            str(root / "include"),
                            "-I",
                            str(root / "include" / "generated"),
                            "-DFE8_EXPANSION_MODERN_BUILD=1",
                            "-DFE8_EXPANSION_DEBUG=1",
                            f"-DFE8_EXPANSION_AUTOPLAY_PLANNER={int(enabled)}",
                            "-c",
                            str(source),
                            "-o",
                            str(output),
                        ],
                        cwd=root,
                        capture_output=True,
                        text=True,
                    )
                    self.assertEqual(
                        completed.returncode,
                        0,
                        completed.stdout + completed.stderr,
                    )
                    profile_objects.append(output)
                sizes = subprocess.run(
                    [size, "-A", *map(str, profile_objects)],
                    cwd=root,
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(sizes.returncode, 0, sizes.stdout + sizes.stderr)
                totals: dict[str, int] = {}
                for section, value in re.findall(
                    r"^(\S+)\s+(\d+)\s+\d+$",
                    sizes.stdout,
                    re.MULTILINE,
                ):
                    totals[section] = totals.get(section, 0) + int(value)
                profile_sections[enabled] = totals
            self.assertEqual(
                profile_sections[True].get("iwram_data", 0),
                profile_sections[False].get("iwram_data", 0),
            )
            self.assertLessEqual(
                profile_sections[True].get("ewram_data", 0)
                - profile_sections[False].get("ewram_data", 0),
                5,
            )

            disabled = temporary_path / "planner-release-disabled.o"
            completed = subprocess.run(
                [
                    compiler,
                    "-mcpu=arm7tdmi",
                    "-mthumb",
                    "-mthumb-interwork",
                    "-mabi=aapcs",
                    "-std=gnu89",
                    "-ffreestanding",
                    "-fno-builtin",
                    "-O2",
                    "-DNDEBUG",
                    "-I",
                    str(root / "include"),
                    "-I",
                    str(root / "include" / "generated"),
                    "-DFE8_EXPANSION_MODERN_BUILD=1",
                    "-DFE8_EXPANSION_AUTOPLAY_PLANNER=0",
                    "-c",
                    str(root / "src" / "expansion_autoplay_planner.c"),
                    "-o",
                    str(disabled),
                ],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(completed.returncode, 0, completed.stdout + completed.stderr)
            disabled_action_semantics = (
                temporary_path / "action-semantics-release-disabled.o"
            )
            completed = subprocess.run(
                [
                    compiler,
                    "-mcpu=arm7tdmi",
                    "-mthumb",
                    "-mthumb-interwork",
                    "-mabi=aapcs",
                    "-std=gnu89",
                    "-ffreestanding",
                    "-fno-builtin",
                    "-O2",
                    "-DNDEBUG",
                    "-I",
                    str(root / "include"),
                    "-I",
                    str(root / "include" / "generated"),
                    "-DFE8_EXPANSION_MODERN_BUILD=1",
                    "-DFE8_EXPANSION_AUTOPLAY_PLANNER=0",
                    "-c",
                    str(root / "src" / "action_semantics.c"),
                    "-o",
                    str(disabled_action_semantics),
                ],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stdout + completed.stderr,
            )
            symbols = subprocess.run(
                [nm, str(disabled), str(disabled_action_semantics)],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(symbols.returncode, 0, symbols.stdout + symbols.stderr)
            self.assertNotIn("gExpansionAutoplayPlanner", symbols.stdout)
            self.assertNotIn("ActionSemantics_", symbols.stdout)

    def test_archival_target_predicates_keep_original_call_graph(self):
        compiler = shutil.which("arm-none-eabi-gcc")
        nm = shutil.which("arm-none-eabi-nm")
        objdump = shutil.which("arm-none-eabi-objdump")
        if compiler is None or nm is None or objdump is None:
            self.skipTest("ARM compiler/binutils unavailable")
        root = TESTS_DIR.parents[2]
        build_root = root / "build"
        build_root.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=build_root) as temporary:
            output = Path(temporary) / "bmtarget-archival.o"
            completed = subprocess.run(
                [
                    compiler,
                    "-mcpu=arm7tdmi",
                    "-mthumb",
                    "-mthumb-interwork",
                    "-mabi=aapcs",
                    "-std=gnu89",
                    "-ffreestanding",
                    "-fno-builtin",
                    "-O2",
                    "-ffunction-sections",
                    "-I",
                    str(root / "include"),
                    "-I",
                    str(root / "include" / "generated"),
                    "-DFE8_ARCHIVAL_BUILD=1",
                    "-c",
                    str(root / "src" / "bmtarget.c"),
                    "-o",
                    str(output),
                ],
                cwd=root,
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                completed.returncode,
                0,
                completed.stdout + completed.stderr,
            )
            symbols = subprocess.run(
                [nm, str(output)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(symbols.returncode, 0, symbols.stderr)
            for predicate in (
                "IsSnagObstacleTarget",
                "IsSnagAttackTargetAt",
                "IsUnitInHealTargetList",
                "HasRangedHealTargetAt",
                "IsUnitInHammerneTargetList",
                "IsUnitInLatonaTargetList",
                "HasLatonaTarget",
                "IsUnitInStaffTargetListAt",
            ):
                self.assertNotIn(predicate, symbols.stdout)
            disassembly = subprocess.run(
                [objdump, "-dr", str(output)],
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                disassembly.returncode,
                0,
                disassembly.stderr,
            )
            heal = disassembly.stdout.split(
                "<TryAddUnitToHealTargetList>:",
                1,
            )[1].split("\n\n", 1)[0]
            hammerne = disassembly.stdout.split(
                "<TryAddUnitToHammerneTargetList>:",
                1,
            )[1].split("\n\n", 1)[0]
            latona = disassembly.stdout.split(
                "<MakeTargetListForLatona>:",
                1,
            )[1].split("\n\n", 1)[0]
            self.assertIn("AreUnitsAllied", heal)
            self.assertIn("GetUnitCurrentHp", heal)
            self.assertIn("IsSameAllegiance", hammerne)
            self.assertIn("IsItemHammernable", hammerne)
            self.assertIn("GetUnitCurrentHp", latona)
            self.assertNotIn("IsUnitIn", heal + hammerne + latona)


@dataclass(frozen=True)
class TransportAcknowledgement:
    command_id: int
    kind: int
    result: int
    rejection: int


@dataclass(frozen=True)
class TransportCompletion:
    command_id: int
    kind: int
    response_frames: int


class PlannerTransportError(RuntimeError):
    def __init__(self, code: str, command_id: int, kind: int) -> None:
        super().__init__(
            f"{code}: command_id={command_id:#x} kind={kind:#x}"
        )
        self.code = code
        self.command_id = command_id
        self.kind = kind


class PlannerProcessTransport:
    def __init__(self, backend: Path, rom: Path) -> None:
        self.process = subprocess.Popen(
            [str(backend), str(rom)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
        )
        self.checkpoint: tuple[int, ...] = ()
        self.command: tuple[int, ...] = ()
        self.last_acknowledgement: TransportAcknowledgement | None = None
        self.last_completion: TransportCompletion | None = None
        self._next_acknowledgement_id = 1
        self.transcript = planner.PlannerTranscript()
        self._transcript_started = False
        self.observation = self._read_state()
        self._begin_transcript(self.observation)

    def _begin_transcript(
        self,
        observation: planner.Observation,
        *,
        allow_uninitialized: bool = False,
    ) -> None:
        if self._transcript_started:
            return
        if not allow_uninitialized and observation.state != 1:
            return
        provenance = (
            {
                "transport": "restricted-libmgba",
                "rom_identity": observation.actual_rom_identity,
                "config_identity": observation.actual_config_identity,
                "scenario_identity": observation.actual_scenario_identity,
                "seed_identity": observation.actual_seed_identity,
                "ready_run_id": observation.run_id,
                "run_id": observation.run_id + 1,
            }
        )
        self.transcript.record_session_observation(
            provenance,
            observation,
            self.checkpoint,
            self.command,
        )
        self._transcript_started = True

    def _read_protocol_line(self) -> tuple[list[str], str]:
        assert self.process.stdout is not None
        line = self.process.stdout.readline()
        if not line:
            stderr = self.process.stderr.read() if self.process.stderr is not None else ""
            raise AssertionError(f"planner transport terminated: {stderr}")
        fields = line.split()
        if fields and fields[0] == "TRANSPORT_ERROR":
            if len(fields) != 4:
                raise AssertionError(f"malformed transport error: {line}")
            self.transcript.record_transport_error(
                fields[1],
                int(fields[2], 16),
                int(fields[3], 16),
            )
            raise PlannerTransportError(
                fields[1],
                int(fields[2], 16),
                int(fields[3], 16),
            )
        return fields, line

    def _read_acknowledgement(self) -> TransportAcknowledgement:
        fields, line = self._read_protocol_line()
        if not fields or fields[0] != "ACK" or len(fields) != 5:
            raise AssertionError(f"unexpected planner acknowledgement: {line}")
        acknowledgement = TransportAcknowledgement(
            *(int(value, 16) for value in fields[1:])
        )
        if acknowledgement.command_id != self._next_acknowledgement_id:
            raise AssertionError(
                "planner acknowledgement generation mismatch: "
                f"expected {self._next_acknowledgement_id:#x}, "
                f"received {acknowledgement.command_id:#x}"
            )
        self._next_acknowledgement_id += 1
        self.last_acknowledgement = acknowledgement
        self.transcript.record_acknowledgement(
            acknowledgement.command_id,
            acknowledgement.kind,
            acknowledgement.result,
            acknowledgement.rejection,
        )
        return acknowledgement

    def _read_completion(
        self,
        acknowledgement: TransportAcknowledgement,
    ) -> TransportCompletion:
        fields, line = self._read_protocol_line()
        if not fields or fields[0] != "COMPLETE" or len(fields) != 4:
            raise AssertionError(f"unexpected planner completion: {line}")
        completion = TransportCompletion(
            *(int(value, 16) for value in fields[1:])
        )
        if (
            completion.command_id != acknowledgement.command_id
            or completion.kind != acknowledgement.kind
        ):
            raise AssertionError(
                "planner completion does not match acknowledgement"
            )
        self.last_completion = completion
        self.transcript.record_completion(
            completion.command_id,
            completion.kind,
            completion.response_frames,
        )
        return completion

    def _read_state(self) -> planner.Observation:
        fields, line = self._read_protocol_line()
        if (
            not fields
            or fields[0] != "OBS"
            or "CHECKPOINT" not in fields
            or "COMMAND" not in fields
        ):
            raise AssertionError(f"unexpected planner transport response: {line}")
        checkpoint_index = fields.index("CHECKPOINT")
        command_index = fields.index("COMMAND")
        observation = planner.parse_transport_observation(
            int(value, 16) for value in fields[1:checkpoint_index]
        )
        self.checkpoint = tuple(
            int(value, 16)
            for value in fields[checkpoint_index + 1 : command_index]
        )
        self.command = tuple(
            int(value, 16) for value in fields[command_index + 1 :]
        )
        self.observation = observation
        return observation

    def _send(
        self,
        line: str,
        *,
        expect_acknowledgement: bool = True,
        transcript_command: dict[str, object] | None = None,
    ) -> planner.Observation:
        assert self.process.stdin is not None
        self.last_acknowledgement = None
        self.last_completion = None
        if transcript_command is not None:
            self._begin_transcript(
                self.observation,
                allow_uninitialized=True,
            )
            self.transcript.reserve_exchange()
            self.transcript.record_command(transcript_command)
        self.process.stdin.write(line + "\n")
        self.process.stdin.flush()
        if expect_acknowledgement:
            acknowledgement = self._read_acknowledgement()
            self._read_completion(acknowledgement)
        observation = self._read_state()
        if self._transcript_started:
            self.transcript.record_observation_page(observation)
            self.transcript.record_settled(
                observation,
                self.checkpoint,
                self.command,
            )
        else:
            self._begin_transcript(observation)
        return observation

    def start(
        self,
        *,
        scenario_identity: int | None = None,
    ) -> planner.Observation:
        ready = self.observation
        expected_identities = (
            ready.actual_rom_identity,
            ready.actual_config_identity,
            (
                ready.actual_scenario_identity
                if scenario_identity is None
                else scenario_identity
            ),
            ready.actual_seed_identity,
        )
        return self._send(
            "START {:08x} {:08x} {:08x} {:08x}".format(
                *expected_identities,
            ),
            transcript_command={
                "kind": planner.CommandKind.START.value,
                "run_id": 0,
                "observation_id": 0,
                "expected_identities": expected_identities,
            },
        )

    def exchange(
        self, command: planner.Command
    ) -> planner.Observation:
        if command.kind is planner.CommandKind.PAGE:
            if command.page_index is None:
                raise AssertionError("PAGE requires page_index")
            return self._send(
                "PAGE {:08x} {:08x} {:08x}".format(
                    command.run_id,
                    command.observation_id,
                    command.page_index,
                ),
                transcript_command={
                    "kind": planner.CommandKind.PAGE.value,
                    "run_id": command.run_id,
                    "observation_id": command.observation_id,
                    "page_index": command.page_index,
                },
            )
        if command.kind is planner.CommandKind.COMMIT:
            if command.action_ordinal is None or command.token is None:
                raise AssertionError("COMMIT requires ordinal and opaque token")
            return self._send(
                "COMMIT {:08x} {:08x} {:08x} "
                "{:08x} {:08x} {:08x} {:08x}".format(
                    command.run_id,
                    command.observation_id,
                    command.action_ordinal,
                    *command.token.words,
                ),
                transcript_command={
                    "kind": planner.CommandKind.COMMIT.value,
                    "run_id": command.run_id,
                    "observation_id": command.observation_id,
                    "action_ordinal": command.action_ordinal,
                    "token": asdict(command.token),
                },
            )
        if command.kind is planner.CommandKind.CANCEL:
            return self._send(
                "CANCEL {:08x} {:08x}".format(
                    command.run_id,
                    command.observation_id,
                ),
                transcript_command={
                    "kind": planner.CommandKind.CANCEL.value,
                    "run_id": command.run_id,
                    "observation_id": command.observation_id,
                },
            )
        raise AssertionError(f"unsupported transport command {command.kind}")

    def record_complete_observation(
        self,
        observation: planner.Observation,
    ) -> None:
        self.transcript.record_complete_and_settled(
            observation,
            self.checkpoint,
            self.command,
        )

    def close(self) -> None:
        if self.process.poll() is None and self.process.stdin is not None:
            try:
                self.process.stdin.write("QUIT\n")
                self.process.stdin.flush()
            except BrokenPipeError:
                pass
        self.process.communicate(timeout=10)


class PlannerLibmGBAIntegrationTests(unittest.TestCase):
    def _build_transport(
        self,
        temporary: str,
        *,
        commit_delay_frames: int = 0,
        stall_after_commit: bool = False,
        ignore_commands: bool = False,
        acknowledgement_frame_limit: int = 120,
        commit_completion_frame_limit: int = 18000,
        transition_subcode: int = 2,
        candidate_mode: int = 0,
        acknowledgement_override: tuple[int, int] | None = None,
        zero_digest: bool = False,
        startup_delay_frames: int = 0,
        startup_state_override: int = 0,
        test_bootstrap: bool = False,
    ) -> tuple[Path, Path]:
        rom = Path(temporary) / "planner-two-chapter.gba"
        elf = Path(temporary) / "planner-two-chapter.elf"
        backend = Path(temporary) / "planner-transport"
        build_production_planner_rom(
            rom,
            elf,
            commit_delay_frames=commit_delay_frames,
            stall_after_commit=stall_after_commit,
            ignore_commands=ignore_commands,
            transition_subcode=transition_subcode,
            candidate_mode=candidate_mode,
            acknowledgement_override=acknowledgement_override,
            zero_digest=zero_digest,
            startup_delay_frames=startup_delay_frames,
            startup_state_override=startup_state_override,
        )
        build_planner_transport_backend(
            backend,
            elf,
            acknowledgement_frame_limit=acknowledgement_frame_limit,
            commit_completion_frame_limit=commit_completion_frame_limit,
            test_bootstrap=test_bootstrap,
        )
        return rom, backend

    def _run_planner(
        self,
        backend: Path,
        rom: Path,
        implementation: planner.ScriptedPlanner | planner.BoundedSearchPlanner,
    ) -> tuple[planner.Observation, tuple[int, ...], bytes]:
        transport = PlannerProcessTransport(backend, rom)
        try:
            waiting = transport.start()
            self.assertEqual(waiting.state, 2)
            first = planner.collect_observation_pages(transport, waiting)
            self.assertEqual(len(first.fields), planner.SEMANTIC_FIELD_COUNT)
            self.assertEqual(len(first.map_cells), 8 * 8)
            self.assertEqual(len(first.units), 1)
            self.assertEqual(len(first.inventory), planner.UNIT_ITEM_COUNT)
            self.assertEqual(
                len(first.resources),
                1
                + planner.CONVOY_ITEM_COUNT
                + planner.AUTOPLAY_TELEMETRY_WORDS,
            )
            self.assertEqual(len(first.flags), 128)
            self.assertEqual(len(first.actions), 63)
            self.assertTrue(
                all(
                    record.action.item_slot is None
                    for record in first.actions
                )
            )
            self.assertEqual(first.resources[0].kind, planner.ValueKind.GOLD)
            self.assertEqual(first.resources[0].value, 1000)
            self.assertEqual(
                first.resources[1].kind,
                planner.ValueKind.CONVOY_ITEM,
            )
            self.assertEqual(first.resources[1].item_id, 1)
            self.assertEqual(first.flags[0].state, 0)
            fields = {field.name: field for field in first.fields}
            self.assertEqual(
                fields["map_dimensions"].value,
                8 | (8 << 16),
            )
            self.assertEqual(
                fields["active_unit"].availability,
                planner.Availability.AVAILABLE,
            )
            self.assertEqual(
                fields["objective_id"].availability,
                planner.Availability.UNSUPPORTED_RULE,
            )
            hidden = next(
                cell for cell in first.map_cells
                if (cell.x, cell.y) == (1, 0)
            )
            self.assertEqual(hidden.availability, planner.Availability.NOT_VISIBLE)
            self.assertEqual(hidden.unit, 0)
            choice = implementation.choose(first)
            self.assertEqual(
                implementation.last_semantic_digest,
                planner._digest(planner._observation_semantics(first)),
            )
            waiting = transport.exchange(
                planner.Command(
                    planner.CommandKind.COMMIT,
                    first.run_id,
                    first.observation_id,
                    choice.ordinal,
                    choice.token,
                )
            )
            self.assertEqual(waiting.state, 2)
            self.assertEqual(waiting.chapter, 2)
            self.assertEqual(len(transport.checkpoint), 13)
            transition_checkpoint = transport.checkpoint
            self.assertEqual(
                transition_checkpoint[:12],
                (
                    0x41504C4E,
                    planner.PROTOCOL_VERSION,
                    52,
                    first.run_id,
                    first.chapter,
                    0,
                    first.chapter_turn,
                    *first.rng_state,
                    first.rng_lcg,
                    first.rng_consumption,
                ),
            )
            self.assertNotEqual(transition_checkpoint[12], 0)
            self.assertEqual(waiting.run_id, first.run_id)

            second = planner.collect_observation_pages(transport, waiting)
            choice = implementation.choose(second)
            committed = transport.exchange(
                planner.Command(
                    planner.CommandKind.COMMIT,
                    second.run_id,
                    second.observation_id,
                    choice.ordinal,
                    choice.token,
                )
            )
            self.assertEqual(committed.state, 2)
            self.assertEqual(transport.checkpoint, transition_checkpoint)
            settled = planner.collect_observation_pages(transport, committed)
            cancelled = transport.exchange(
                planner.Command(
                    planner.CommandKind.CANCEL,
                    settled.run_id,
                    settled.observation_id,
                )
            )
            self.assertEqual(cancelled.state, 4)
            self.assertTrue(all(value == 0 for value in transport.checkpoint))
            return (
                settled,
                transition_checkpoint,
                transport.transcript.export(),
            )
        finally:
            transport.close()

    def test_host_driven_production_mailbox_replays_two_chapters(self):
        root = TESTS_DIR.parents[2] / "build" / "test-artifacts" / "autoplay-planner"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            try:
                rom, backend = self._build_transport(temporary)
            except RuntimeError as error:
                if (
                    "planner runtime toolchain unavailable" in str(error)
                    or "planner transport host compiler unavailable" in str(error)
                ):
                    self.skipTest(str(error))
                raise
            symbols = subprocess.run(
                ["arm-none-eabi-nm", str(rom.with_suffix(".elf"))],
                capture_output=True,
                text=True,
            )
            self.assertEqual(
                symbols.returncode,
                0,
                symbols.stdout + symbols.stderr,
            )
            for symbol in (
                "Event2A_MoveToChapter",
                "EventEngine_OnEnd",
                "EndBMapMainForChapterTransition",
                "ExpansionAutoplayPlanner_RecordCampaignCheckpoint",
            ):
                self.assertRegex(symbols.stdout, rf"\b{symbol}(?:\n|$)")
            scripted = self._run_planner(
                backend, rom, planner.ScriptedPlanner()
            )
            searched = self._run_planner(
                backend, rom, planner.BoundedSearchPlanner(max_nodes=512)
            )
            self.assertEqual(scripted, searched)
            for recorded in (scripted[2], searched[2]):
                self.assertEqual(
                    planner.replay_transcript_on_clean_transport(
                        recorded,
                        lambda: PlannerProcessTransport(backend, rom),
                    ),
                    recorded,
                )
            imported = planner.PlannerTranscript.import_bytes(scripted[2])
            self.assertEqual(imported.export(), scripted[2])
            identity_tampered = json.loads(scripted[2])
            start_command = next(
                event["command"]
                for event in identity_tampered["events"]
                if event["event"] == "command"
                    and event["command"]["kind"]
                        == planner.CommandKind.START.value
            )
            start_command["expected_identities"][2] ^= 1
            previous = "0" * 64
            for sequence, event in enumerate(
                identity_tampered["events"]
            ):
                event.pop("event_digest", None)
                event["sequence"] = sequence
                event["previous_digest"] = previous
                event["event_digest"] = planner._digest(event)
                previous = event["event_digest"]
            with self.assertRaisesRegex(
                planner.PlannerError,
                "START command session identity mismatch",
            ):
                planner.PlannerTranscript.import_bytes(
                    planner._canonical(identity_tampered)
                )
            complete_events = [
                event
                for event in imported.events
                if event["event"] == "observation_complete"
            ]
            self.assertGreaterEqual(len(complete_events), 3)
            self.assertTrue(complete_events[-1]["observation"]["inventory"])
            self.assertTrue(complete_events[-1]["observation"]["resources"])
            self.assertTrue(complete_events[-1]["observation"]["flags"])
            settled_events = [
                event
                for event in imported.events
                if event["event"] == "settled"
                and event["telemetry"]
            ]
            self.assertTrue(settled_events)
            self.assertEqual(len(settled_events[-1]["telemetry"]), 16)

    def test_clean_transport_replays_rejection_and_cancel(self):
        root = (
            TESTS_DIR.parents[2]
            / "build"
            / "test-artifacts"
            / "autoplay-planner"
        )
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            try:
                rom, backend = self._build_transport(temporary)
            except RuntimeError as error:
                if (
                    "planner runtime toolchain unavailable" in str(error)
                    or "planner transport host compiler unavailable"
                        in str(error)
                ):
                    self.skipTest(str(error))
                raise
            transport = PlannerProcessTransport(backend, rom)
            try:
                first = planner.collect_observation_pages(
                    transport,
                    transport.start(),
                )
                choice = planner.ScriptedPlanner().choose(first)
                stale_page = transport.exchange(
                    planner.Command(
                        planner.CommandKind.PAGE,
                        first.run_id,
                        first.observation_id + 1,
                        page_index=1,
                    )
                )
                self.assertEqual(stale_page.rejection, 2)
                forged = transport.exchange(
                    planner.Command(
                        planner.CommandKind.COMMIT,
                        first.run_id,
                        first.observation_id,
                        choice.ordinal,
                        planner.OpaqueToken(
                            choice.token.word0 ^ 1,
                            choice.token.word1,
                            choice.token.word2,
                            choice.token.word3,
                        ),
                    )
                )
                self.assertEqual(forged.rejection, 4)
                cancelled = transport.exchange(
                    planner.Command(
                        planner.CommandKind.CANCEL,
                        first.run_id,
                        first.observation_id,
                    )
                )
                self.assertEqual(cancelled.state, 4)
                recorded = transport.transcript.export()
            finally:
                transport.close()
            self.assertEqual(
                planner.replay_transcript_on_clean_transport(
                    recorded,
                    lambda: PlannerProcessTransport(backend, rom),
                ),
                recorded,
            )

            tampered = json.loads(recorded)
            commit = next(
                event["command"]
                for event in tampered["events"]
                if event["event"] == "command"
                    and event["command"]["kind"]
                        == planner.CommandKind.COMMIT.value
            )
            commit["observation_id"] += 1
            previous = "0" * 64
            for sequence, event in enumerate(tampered["events"]):
                event.pop("event_digest", None)
                event["sequence"] = sequence
                event["previous_digest"] = previous
                event["event_digest"] = planner._digest(event)
                previous = event["event_digest"]
            factory_calls = 0

            def factory():
                nonlocal factory_calls
                factory_calls += 1
                return PlannerProcessTransport(backend, rom)

            with self.assertRaisesRegex(
                planner.PlannerError,
                "command observation identity mismatch",
            ):
                planner.replay_transcript_on_clean_transport(
                    planner._canonical(tampered),
                    factory,
                )
            self.assertEqual(factory_calls, 0)

    def test_world_map_transition_records_settled_checkpoint(self):
        root = (
            TESTS_DIR.parents[2]
            / "build"
            / "test-artifacts"
            / "autoplay-planner"
        )
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            try:
                rom, backend = self._build_transport(
                    temporary,
                    transition_subcode=1,
                )
            except RuntimeError as error:
                if (
                    "planner runtime toolchain unavailable" in str(error)
                    or "planner transport host compiler unavailable"
                        in str(error)
                ):
                    self.skipTest(str(error))
                raise
            _, checkpoint, transcript = self._run_planner(
                backend,
                rom,
                planner.ScriptedPlanner(),
            )
            self.assertEqual(checkpoint[4], 1)
            self.assertNotEqual(checkpoint[12], 0)
            self.assertEqual(
                planner.PlannerTranscript.import_bytes(transcript).export(),
                transcript,
            )

    def test_exhausted_runs_restore_without_fallback_or_reentry(self):
        root = (
            TESTS_DIR.parents[2]
            / "build"
            / "test-artifacts"
            / "autoplay-planner"
        )
        root.mkdir(parents=True, exist_ok=True)
        for candidate_mode, rejection in ((1, 5), (2, 7)):
            with self.subTest(candidate_mode=candidate_mode):
                with tempfile.TemporaryDirectory(dir=root) as temporary:
                    try:
                        rom, backend = self._build_transport(
                            temporary,
                            candidate_mode=candidate_mode,
                        )
                    except RuntimeError as error:
                        if (
                            "planner runtime toolchain unavailable"
                                in str(error)
                            or "planner transport host compiler unavailable"
                                in str(error)
                        ):
                            self.skipTest(str(error))
                        raise
                    transport = PlannerProcessTransport(backend, rom)
                    try:
                        exhausted = transport.start()
                        self.assertEqual(exhausted.state, 5)
                        self.assertEqual(exhausted.rejection, rejection)
                        self.assertEqual(exhausted.page_count, 1)
                        self.assertTrue(
                            all(
                                value == 0
                                for value in transport.checkpoint
                            )
                        )
                        stale_start = transport.start()
                        self.assertEqual(stale_start.state, 5)
                        self.assertEqual(stale_start.rejection, 9)
                        self.assertIsNotNone(
                            transport.last_acknowledgement
                        )
                        self.assertEqual(
                            transport.last_acknowledgement.result,
                            0,
                        )
                        self.assertEqual(
                            transport.last_acknowledgement.rejection,
                            9,
                        )
                    finally:
                        transport.close()

    def test_available_zero_digests_round_trip_live_transport(self):
        root = (
            TESTS_DIR.parents[2]
            / "build"
            / "test-artifacts"
            / "autoplay-planner"
        )
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            try:
                rom, backend = self._build_transport(
                    temporary,
                    zero_digest=True,
                )
            except RuntimeError as error:
                if (
                    "planner runtime toolchain unavailable" in str(error)
                    or "planner transport host compiler unavailable"
                        in str(error)
                ):
                    self.skipTest(str(error))
                raise
            for implementation in (
                planner.ScriptedPlanner(),
                planner.BoundedSearchPlanner(max_nodes=512),
            ):
                transport = PlannerProcessTransport(backend, rom)
                try:
                    first = planner.collect_observation_pages(
                        transport,
                        transport.start(),
                    )
                    fields = {field.name: field for field in first.fields}
                    self.assertEqual(
                        fields["flags_digest"].availability,
                        planner.Availability.AVAILABLE,
                    )
                    self.assertEqual(fields["flags_digest"].value, 0)
                    self.assertEqual(
                        fields["resource_digest"].availability,
                        planner.Availability.AVAILABLE,
                    )
                    self.assertEqual(fields["resource_digest"].value, 0)
                    choice = implementation.choose(first)
                    cancelled = transport.exchange(
                        planner.Command(
                            planner.CommandKind.CANCEL,
                            first.run_id,
                            first.observation_id,
                        )
                    )
                    self.assertEqual(cancelled.state, 4)
                    exported = transport.transcript.export()
                    self.assertEqual(
                        planner.PlannerTranscript.import_bytes(
                            exported
                        ).export(),
                        exported,
                    )
                    self.assertIsNotNone(choice)
                finally:
                    transport.close()

    def test_commit_waits_beyond_legacy_120_frame_window(self):
        root = TESTS_DIR.parents[2] / "build" / "test-artifacts" / "autoplay-planner"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            try:
                rom, backend = self._build_transport(
                    temporary,
                    commit_delay_frames=180,
                    commit_completion_frame_limit=600,
                )
            except RuntimeError as error:
                if (
                    "planner runtime toolchain unavailable" in str(error)
                    or "planner transport host compiler unavailable" in str(error)
                ):
                    self.skipTest(str(error))
                raise

            for implementation in (
                planner.ScriptedPlanner(),
                planner.BoundedSearchPlanner(max_nodes=512),
            ):
                transport = PlannerProcessTransport(backend, rom)
                try:
                    waiting = transport.start()
                    complete = planner.collect_observation_pages(
                        transport,
                        waiting,
                    )
                    choice = implementation.choose(complete)
                    followup = transport.exchange(
                        planner.Command(
                            planner.CommandKind.COMMIT,
                            complete.run_id,
                            complete.observation_id,
                            choice.ordinal,
                            choice.token,
                        )
                    )
                    self.assertEqual(followup.state, 2)
                    self.assertEqual(followup.chapter, 2)
                    acknowledgement = transport.last_acknowledgement
                    completion = transport.last_completion
                    self.assertIsNotNone(acknowledgement)
                    self.assertIsNotNone(completion)
                    self.assertEqual(acknowledgement.kind, 2)
                    self.assertEqual(acknowledgement.result, 1)
                    self.assertEqual(acknowledgement.rejection, 0)
                    self.assertGreater(completion.response_frames, 120)
                    self.assertEqual(
                        completion.command_id,
                        acknowledgement.command_id,
                    )
                finally:
                    transport.close()

    def test_acknowledged_commit_timeout_never_emits_stale_observation(self):
        root = TESTS_DIR.parents[2] / "build" / "test-artifacts" / "autoplay-planner"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            try:
                rom, backend = self._build_transport(
                    temporary,
                    stall_after_commit=True,
                    commit_completion_frame_limit=60,
                )
            except RuntimeError as error:
                if (
                    "planner runtime toolchain unavailable" in str(error)
                    or "planner transport host compiler unavailable" in str(error)
                ):
                    self.skipTest(str(error))
                raise

            transport = PlannerProcessTransport(backend, rom)
            try:
                waiting = transport.start()
                complete = planner.collect_observation_pages(transport, waiting)
                choice = planner.ScriptedPlanner().choose(complete)
                with self.assertRaises(PlannerTransportError) as raised:
                    transport.exchange(
                        planner.Command(
                            planner.CommandKind.COMMIT,
                            complete.run_id,
                            complete.observation_id,
                            choice.ordinal,
                            choice.token,
                        )
                    )
                self.assertEqual(
                    raised.exception.code,
                    "ACTION_COMPLETION_TIMEOUT",
                )
                self.assertIsNotNone(transport.last_acknowledgement)
                self.assertEqual(transport.last_acknowledgement.kind, 2)
                self.assertEqual(transport.last_acknowledgement.result, 1)
                self.assertEqual(transport.last_acknowledgement.rejection, 0)
                self.assertIsNone(transport.last_completion)
                remaining_stdout, _ = transport.process.communicate(timeout=10)
                self.assertEqual(remaining_stdout, "")
                self.assertEqual(transport.process.returncode, 3)
            finally:
                transport.close()

    def test_unacknowledged_command_returns_typed_timeout(self):
        root = TESTS_DIR.parents[2] / "build" / "test-artifacts" / "autoplay-planner"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            try:
                rom, backend = self._build_transport(
                    temporary,
                    ignore_commands=True,
                    acknowledgement_frame_limit=20,
                )
            except RuntimeError as error:
                if (
                    "planner runtime toolchain unavailable" in str(error)
                    or "planner transport host compiler unavailable" in str(error)
                ):
                    self.skipTest(str(error))
                raise

            transport = PlannerProcessTransport(backend, rom)
            try:
                with self.assertRaises(PlannerTransportError) as raised:
                    transport.start()
                self.assertEqual(
                    raised.exception.code,
                    "COMMAND_ACK_TIMEOUT",
                )
                self.assertIsNone(transport.last_acknowledgement)
                self.assertIsNone(transport.last_completion)
                remaining_stdout, _ = transport.process.communicate(timeout=10)
                self.assertEqual(remaining_stdout, "")
                self.assertEqual(transport.process.returncode, 3)
            finally:
                transport.close()

    def test_backend_requires_exact_ready_before_stdin(self):
        root = (
            TESTS_DIR.parents[2]
            / "build"
            / "test-artifacts"
            / "autoplay-planner"
        )
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            immediate_dir = Path(temporary) / "immediate"
            immediate_dir.mkdir()
            rom, backend = self._build_transport(str(immediate_dir))
            transport = PlannerProcessTransport(backend, rom)
            try:
                self.assertEqual(transport.observation.state, 1)
            finally:
                transport.close()

            delayed_dir = Path(temporary) / "delayed"
            delayed_dir.mkdir()
            rom, backend = self._build_transport(
                str(delayed_dir),
                startup_delay_frames=8,
                test_bootstrap=True,
            )
            transport = PlannerProcessTransport(backend, rom)
            try:
                self.assertEqual(transport.observation.state, 1)
            finally:
                transport.close()

            failure_cases = (
                ("delayed-no-bootstrap", 8, 0, False, "startup"),
                ("never-ready", 10000, 0, True, "bootstrap"),
                ("wrong-state", 0, 2, True, "bootstrap"),
                ("exhausted-state", 0, 5, True, "bootstrap"),
            )
            for (
                name,
                delay,
                state,
                bootstrap,
                diagnostic,
            ) in failure_cases:
                with self.subTest(startup=name):
                    case_dir = Path(temporary) / name
                    case_dir.mkdir()
                    rom, backend = self._build_transport(
                        str(case_dir),
                        startup_delay_frames=delay,
                        startup_state_override=state,
                        test_bootstrap=bootstrap,
                    )
                    completed = subprocess.run(
                        [str(backend), str(rom)],
                        input="READ\nSTART 0 0 0 0\n",
                        capture_output=True,
                        text=True,
                        timeout=20,
                    )
                    self.assertEqual(completed.returncode, 3)
                    self.assertEqual(completed.stdout, "")
                    self.assertIn(diagnostic, completed.stderr)

    def test_invalid_ack_is_rejected_before_ack_or_observation(self):
        root = (
            TESTS_DIR.parents[2]
            / "build"
            / "test-artifacts"
            / "autoplay-planner"
        )
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            try:
                rom, backend = self._build_transport(
                    temporary,
                    acknowledgement_override=(0, 0xFFFFFFFF),
                )
            except RuntimeError as error:
                if (
                    "planner runtime toolchain unavailable" in str(error)
                    or "planner transport host compiler unavailable"
                        in str(error)
                ):
                    self.skipTest(str(error))
                raise
            transport = PlannerProcessTransport(backend, rom)
            try:
                with self.assertRaises(PlannerTransportError) as raised:
                    transport.start()
                self.assertEqual(
                    raised.exception.code,
                    "INVALID_COMMAND_ACK",
                )
                self.assertIsNone(transport.last_acknowledgement)
                self.assertIsNone(transport.last_completion)
                remaining_stdout, _ = transport.process.communicate(
                    timeout=10
                )
                self.assertEqual(remaining_stdout, "")
                self.assertEqual(transport.process.returncode, 3)
            finally:
                transport.close()

    def test_restricted_backend_rejects_frame_and_key_controls(self):
        root = (
            TESTS_DIR.parents[2]
            / "build"
            / "test-artifacts"
            / "autoplay-planner"
        )
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            try:
                rom, backend = self._build_transport(temporary)
            except RuntimeError as error:
                if (
                    "planner runtime toolchain unavailable" in str(error)
                    or "planner transport host compiler unavailable"
                        in str(error)
                ):
                    self.skipTest(str(error))
                raise
            transport = PlannerProcessTransport(backend, rom)
            try:
                observation_before = transport.observation
                checkpoint_before = transport.checkpoint
                command_before = transport.command
                transcript_before = transport.transcript.export()
                assert transport.process.stdin is not None
                assert transport.process.stdout is not None
                for raw_command in (
                    "STEP",
                    "RUN 100 1",
                    "KEYS 3ff",
                ):
                    transport.process.stdin.write(raw_command + "\n")
                    transport.process.stdin.flush()
                    self.assertEqual(
                        transport.process.stdout.readline(),
                        "ERROR unknown typed command\n",
                    )
                    transport.process.stdin.write("READ\n")
                    transport.process.stdin.flush()
                    observed = transport._read_state()
                    self.assertEqual(observed, observation_before)
                    self.assertEqual(
                        transport.checkpoint,
                        checkpoint_before,
                    )
                    self.assertEqual(transport.command, command_before)
                    self.assertEqual(
                        transport.transcript.export(),
                        transcript_before,
                    )

                waiting = transport.start()
                cancelled = transport.exchange(
                    planner.Command(
                        planner.CommandKind.CANCEL,
                        waiting.run_id,
                        waiting.observation_id,
                    )
                )
                self.assertEqual(cancelled.state, 4)
                encoded = json.loads(transport.transcript.export())
            finally:
                transport.close()

            for unsupported_kind in ("RUN", 0xFFFFFFFF):
                with self.subTest(unsupported_kind=unsupported_kind):
                    tampered = json.loads(planner._canonical(encoded))
                    command_event = next(
                        event
                        for event in tampered["events"]
                        if event["event"] == "command"
                    )
                    command_event["command"]["kind"] = unsupported_kind
                    previous = "0" * 64
                    for sequence, event in enumerate(tampered["events"]):
                        event.pop("event_digest", None)
                        event["sequence"] = sequence
                        event["previous_digest"] = previous
                        event["event_digest"] = planner._digest(event)
                        previous = event["event_digest"]
                    factory_calls = 0

                    def factory():
                        nonlocal factory_calls
                        factory_calls += 1
                        return PlannerProcessTransport(backend, rom)

                    with self.assertRaisesRegex(
                        planner.PlannerError,
                        "unsupported command kind",
                    ):
                        planner.replay_transcript_on_clean_transport(
                            planner._canonical(tampered),
                            factory,
                        )
                    self.assertEqual(factory_calls, 0)

    def test_production_transcript_capacity_rejects_before_mailbox_write(self):
        root = TESTS_DIR.parents[2] / "build" / "test-artifacts" / "autoplay-planner"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            try:
                rom, backend = self._build_transport(temporary)
            except RuntimeError as error:
                if (
                    "planner runtime toolchain unavailable" in str(error)
                    or "planner transport host compiler unavailable" in str(error)
                ):
                    self.skipTest(str(error))
                raise

            transport = PlannerProcessTransport(backend, rom)
            try:
                transport.transcript = planner.PlannerTranscript(
                    max_bytes=planner.MAX_TRANSCRIPT_EXCHANGE_BYTES
                )
                transport._transcript_started = False
                transport._begin_transcript(
                    transport.observation,
                    allow_uninitialized=True,
                )
                transcript_before = transport.transcript.export()
                command_before = transport.command
                observation_before = transport.observation
                with self.assertRaisesRegex(
                    planner.PlannerError,
                    planner.Rejection.RESOURCE_LIMIT.value,
                ):
                    transport.start()
                self.assertEqual(transport.transcript.export(), transcript_before)
                self.assertEqual(transport.command, command_before)
                self.assertIs(transport.observation, observation_before)
                self.assertIsNone(transport.last_acknowledgement)
                self.assertIsNone(transport.last_completion)
            finally:
                transport.close()

    def test_host_driven_transport_rejects_and_times_out(self):
        root = TESTS_DIR.parents[2] / "build" / "test-artifacts" / "autoplay-planner"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            try:
                rom, backend = self._build_transport(temporary)
            except RuntimeError as error:
                if (
                    "planner runtime toolchain unavailable" in str(error)
                    or "planner transport host compiler unavailable" in str(error)
                ):
                    self.skipTest(str(error))
                raise

            transport = PlannerProcessTransport(backend, rom)
            try:
                wrong_scenario = (
                    transport.observation.actual_scenario_identity ^ 1
                )
                acknowledgements = []
                for _ in range(3):
                    rejected = transport.start(
                        scenario_identity=wrong_scenario
                    )
                    self.assertEqual(rejected.rejection, 9)
                    acknowledgements.append(
                        transport.last_acknowledgement
                    )
                (
                    first_rejection_ack,
                    second_rejection_ack,
                    third_rejection_ack,
                ) = acknowledgements
                self.assertIsNotNone(first_rejection_ack)
                self.assertIsNotNone(second_rejection_ack)
                self.assertIsNotNone(third_rejection_ack)
                self.assertEqual(first_rejection_ack.rejection, 9)
                self.assertEqual(second_rejection_ack.rejection, 9)
                self.assertEqual(third_rejection_ack.rejection, 9)
                self.assertEqual(
                    (
                        first_rejection_ack.command_id + 1,
                        second_rejection_ack.command_id + 1,
                    ),
                    (
                        second_rejection_ack.command_id,
                        third_rejection_ack.command_id,
                    ),
                )
                waiting = transport.start()
                start_acknowledgement = transport.last_acknowledgement
                start_completion = transport.last_completion
                self.assertIsNotNone(start_acknowledgement)
                self.assertIsNotNone(start_completion)
                self.assertEqual(start_acknowledgement.kind, 1)
                self.assertEqual(start_acknowledgement.result, 1)
                self.assertLess(start_completion.response_frames, 120)
                complete = planner.collect_observation_pages(transport, waiting)
                page_acknowledgement = transport.last_acknowledgement
                page_completion = transport.last_completion
                self.assertIsNotNone(page_acknowledgement)
                self.assertIsNotNone(page_completion)
                self.assertEqual(page_acknowledgement.kind, 4)
                self.assertEqual(page_acknowledgement.result, 1)
                self.assertLess(page_completion.response_frames, 120)
                choice = planner.ScriptedPlanner().choose(complete)
                forged = transport.exchange(
                    planner.Command(
                        planner.CommandKind.COMMIT,
                        complete.run_id,
                        complete.observation_id,
                        choice.ordinal,
                        planner.OpaqueToken(
                            choice.token.word0,
                            choice.token.word1,
                            choice.token.word2,
                            choice.token.word3 ^ 1,
                        ),
                    )
                )
                self.assertEqual(forged.rejection, 4)
                cancelled = transport.exchange(
                    planner.Command(
                        planner.CommandKind.CANCEL,
                        complete.run_id,
                        complete.observation_id,
                    )
                )
                self.assertEqual(cancelled.state, 4)
                self.assertEqual(cancelled.rejection, 8)
                cancel_acknowledgement = transport.last_acknowledgement
                cancel_completion = transport.last_completion
                self.assertIsNotNone(cancel_acknowledgement)
                self.assertIsNotNone(cancel_completion)
                self.assertEqual(cancel_acknowledgement.kind, 3)
                self.assertEqual(cancel_acknowledgement.rejection, 8)
                self.assertLess(cancel_completion.response_frames, 120)
                self.assertTrue(all(value == 0 for value in transport.checkpoint))
            finally:
                transport.close()

            transport = PlannerProcessTransport(backend, rom)
            try:
                waiting = transport.start()
                complete = planner.collect_observation_pages(transport, waiting)
                choice = planner.ScriptedPlanner().choose(complete)
                continued = transport.exchange(
                    planner.Command(
                        planner.CommandKind.COMMIT,
                        complete.run_id,
                        complete.observation_id,
                        choice.ordinal,
                        choice.token,
                    )
                )
                self.assertEqual(continued.state, 2)
                self.assertEqual(transport.checkpoint[0], 0x41504C4E)
                self.assertEqual(transport.checkpoint[4], 1)
                cancelled = transport.exchange(
                    planner.Command(
                        planner.CommandKind.CANCEL,
                        continued.run_id,
                        continued.observation_id,
                    )
                )
                self.assertEqual(cancelled.state, 4)
                self.assertEqual(cancelled.rejection, 8)
                self.assertTrue(all(value == 0 for value in transport.checkpoint))
            finally:
                transport.close()

            transport = PlannerProcessTransport(backend, rom)
            try:
                waiting = transport.start()
                complete = planner.collect_observation_pages(transport, waiting)
                choice = planner.ScriptedPlanner().choose(complete)
                waiting = transport.exchange(
                    planner.Command(
                        planner.CommandKind.COMMIT,
                        complete.run_id,
                        complete.observation_id,
                        choice.ordinal,
                        choice.token,
                    )
                )
                self.assertEqual(waiting.state, 2)
                self.assertEqual(transport.checkpoint[0], 0x41504C4E)
                self.assertEqual(transport.checkpoint[4], 1)
                for _ in range(300):
                    waiting = transport.exchange(
                        planner.Command(
                            planner.CommandKind.PAGE,
                            waiting.run_id,
                            waiting.observation_id,
                            page_index=0,
                        )
                    )
                    if waiting.state == 4:
                        break
                self.assertEqual(waiting.state, 4)
                self.assertEqual(waiting.rejection, 10)
                self.assertTrue(all(value == 0 for value in transport.checkpoint))
            finally:
                transport.close()

    @unittest.skipUnless(
        os.environ.get("PLANNER_PRODUCTION_ROM")
        and os.environ.get("PLANNER_PRODUCTION_ELF"),
        "enabled production ROM/ELF not supplied",
    )
    def test_enabled_production_rom_executes_host_selected_action(self):
        rom = Path(os.environ["PLANNER_PRODUCTION_ROM"])
        elf = Path(os.environ["PLANNER_PRODUCTION_ELF"])
        root = TESTS_DIR.parents[2] / "build" / "test-artifacts" / "autoplay-planner"
        root.mkdir(parents=True, exist_ok=True)
        with tempfile.TemporaryDirectory(dir=root) as temporary:
            blocked_backend = Path(temporary) / "planner-transport-blocked"
            build_planner_transport_backend(blocked_backend, elf)
            blocked = subprocess.run(
                [str(blocked_backend), str(rom)],
                input="READ\n",
                capture_output=True,
                text=True,
                timeout=20,
            )
            self.assertEqual(blocked.returncode, 3)
            self.assertEqual(blocked.stdout, "")
            self.assertIn("startup is not READY", blocked.stderr)

            backend = Path(temporary) / "planner-transport"
            build_planner_transport_backend(
                backend,
                elf,
                test_bootstrap=True,
            )
            transport = PlannerProcessTransport(backend, rom)
            try:
                self.assertEqual(transport.observation.state, 1)
                waiting = transport.start()
                self.assertEqual(waiting.state, 2)
                complete = planner.collect_observation_pages(transport, waiting)
                self.assertGreater(len(complete.map_cells), 0)
                self.assertGreater(len(complete.units), 0)
                self.assertGreater(len(complete.actions), 0)
                choice = planner.ScriptedPlanner().choose(complete)
                actor_before = next(
                    unit for unit in complete.units if unit.slot == choice.action.actor
                )
                self.assertNotEqual(actor_before.position, choice.action.destination)

                forged = transport.exchange(
                    planner.Command(
                        planner.CommandKind.COMMIT,
                        complete.run_id,
                        complete.observation_id,
                        choice.ordinal,
                        planner.OpaqueToken(
                            choice.token.word0 ^ 1,
                            choice.token.word1,
                            choice.token.word2,
                            choice.token.word3,
                        ),
                    )
                )
                self.assertEqual(forged.rejection, 4)
                accepted = transport.exchange(
                    planner.Command(
                        planner.CommandKind.COMMIT,
                        complete.run_id,
                        complete.observation_id,
                        choice.ordinal,
                        choice.token,
                    )
                )
                self.assertEqual(accepted.state, 2)
                self.assertNotEqual(
                    accepted.observation_id,
                    complete.observation_id,
                )
                followup = planner.collect_observation_pages(
                    transport, accepted
                )
                actor_after = next(
                    unit for unit in followup.units if unit.slot == choice.action.actor
                )
                self.assertEqual(actor_after.position, choice.action.destination)
                cancelled = transport.exchange(
                    planner.Command(
                        planner.CommandKind.CANCEL,
                        followup.run_id,
                        followup.observation_id,
                    )
                )
                self.assertEqual(cancelled.state, 4)
                transcript = transport.transcript.export()
                imported = planner.PlannerTranscript.import_bytes(transcript)
                self.assertEqual(imported.export(), transcript)
                self.assertEqual(
                    imported.events[0]["event"],
                    "session",
                )
            finally:
                transport.close()


if __name__ == "__main__":
    unittest.main()
