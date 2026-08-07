"""Deterministic evidence promotion and authored-translation queue generation."""

from __future__ import annotations

import hashlib
import json
import re
from collections import Counter, defaultdict
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple

from scripts.localization.game_catalog.build import encode_canonical_text
from scripts.localization.game_catalog.english_source import (
    load_english_definitions,
    load_english_source_entries,
)

from .crosswalk import build_crosswalk_coverage_report, canonical_json_bytes
from .febuilder import validate_febuilder_evidence_document
from .mapping import format_message_id, validate_mapping_document
from .parsers import parse_hash_indexed
from .raw_providers import load_ja_raw_providers, resolve_ja_raw_text
from .structural_completion import (
    _family_for_sites,
    validate_structural_completion_evidence,
)

FINAL_MAPPING_PIPELINE = "fe8u-final-mapping-v1"
FINAL_REPORT_SCHEMA_VERSION = 1
FINAL_REPORT_KIND = "fe8u-final-mapping-report"
AUTHORED_QUEUE_SCHEMA_VERSION = 1
AUTHORED_QUEUE_KIND = "fe8u-authored-translation-queue"

_TOKEN_RE = re.compile(r"\[([^\[\]\r\n]+)\]")
_C_COMMENT_RE = re.compile(r"/\*.*?\*/|//[^\n]*", re.DOTALL)
_IDENTIFIER_RE = re.compile(r"[^a-z0-9]+")
_PLACEHOLDER_TOKENS = frozenset(("G", "Item", "SetName", "Tact"))
_PRINTABLE_TOKENS = {
    "AccentedE": "e",
    "DashedLine": "-",
    "LQuote": '"',
    "RQuote": '"',
    "TAB": "\u3000",
}


class FinalMappingError(ValueError):
    """Raised when promotion evidence cannot safely update the target map."""


def _indexed_source(source_id: str) -> Dict[str, str]:
    return {"id": source_id, "kind": "indexed", "layout": "FE8J"}


_CONTEXTUAL_COLLISION_RESOLUTIONS: Dict[str, Dict[str, Any]] = {
    "0x0010": {
        "source_id": "0x080F",
        "confidence": "manual",
        "subsystem": "menu-definition",
        "evidence_kind": "popup-state-semantic-split",
        "source_key": "NewPopup2 convoy result/past-tense state",
        "rationale": (
            "The FE8U popup is the completed 'Sent' state; FE8J 0x080F is the "
            "completed convoy sentence while 0x080C is the in-progress fragment."
        ),
    },
    "0x053B": {
        "source_id": "0x04D0",
        "confidence": "manual",
        "subsystem": "menu-definition",
        "evidence_kind": "augury-panel-semantic-slot",
        "source_key": "DrawAuguryResultPanel.assets",
        "rationale": (
            "The target label is total Assets; FE8J 0x04D0 is total assets, "
            "whereas 0x04C2 is the current-money label."
        ),
    },
    "0x053D": {
        "source_id": "0x04D2",
        "confidence": "manual",
        "subsystem": "menu-definition",
        "evidence_kind": "augury-panel-semantic-slot",
        "source_key": "DrawAuguryResultPanel.total",
        "rationale": (
            "The target is the generic augury Total label; FE8J 0x04D2 is the "
            "same panel label, while 0x04C1 and 0x04CF are turn-specific."
        ),
    },
    "0x0581": {
        "source_id": "0x053F",
        "confidence": "high",
        "subsystem": "menu-definition",
        "evidence_kind": "preparation-table-slot",
        "source_key": "gPrepscreen_6[2][1]",
        "rationale": (
            "The named preparation table's save-help slot carries the long "
            "restart/save explanation; FE8J 0x0612 is only the Support label."
        ),
    },
    "0x0601": {
        "source_id": "0x0593",
        "confidence": "high",
        "subsystem": "help-tutorial",
        "evidence_kind": "guide-item-name-slot",
        "source_key": "bmguide.itemName=Suspend",
        "rationale": (
            "The guide item-name slot is Suspend. The indexed and raw FEBuilder "
            "candidates encode the same regional label; the bounded indexed "
            "provider is selected for the compressed target."
        ),
    },
    "0x060C": {
        "source_id": "0x062A",
        "confidence": "manual",
        "subsystem": "help-tutorial",
        "evidence_kind": "guide-item-name-semantic-split",
        "source_key": "bmguide.itemName=Retreat",
        "rationale": (
            "The target is the plain Retreat guide item name; FE8J 0x062A is "
            "the plain label while 0x059E is the longer 'Retreat command' label."
        ),
    },
    "0x0658": {
        "source_id": "0x05EA",
        "confidence": "high",
        "subsystem": "world-map",
        "evidence_kind": "world-map-node-slot",
        "source_key": "gWMNodeData[NODE_RENVALL_08].nameTextId",
        "rationale": (
            "The target is the NODE_RENVALL_08 world-map name slot. FE8J "
            "0x05E9 is consumed by the Chapter 5x event; the duplicate-payload "
            "0x05EA remains the node-name provider."
        ),
    },
    "0x072A": {
        "source_id": "0x06B2",
        "confidence": "high",
        "subsystem": "help-tutorial",
        "evidence_kind": "class-reel-keyed-slot",
        "source_key": "gClassReelData[CLASS_ELDER_BAEL].description",
        "rationale": (
            "The target is the Elder Bael class-reel description. FE8J 0x02CA "
            "is the class-data description; 0x06B2 is the reel-table duplicate."
        ),
    },
    "0x0730": {
        "source_id": "0x06B8",
        "confidence": "high",
        "subsystem": "help-tutorial",
        "evidence_kind": "class-reel-keyed-slot",
        "source_key": "gClassReelData[CLASS_MOGALL].description",
        "rationale": (
            "The target is the Mogall class-reel description. FE8J 0x02D0 is "
            "the class-data description; 0x06B8 is the reel-table duplicate."
        ),
    },
    "0x0732": {
        "source_id": "0x06BA",
        "confidence": "high",
        "subsystem": "help-tutorial",
        "evidence_kind": "class-reel-keyed-slot",
        "source_key": "gClassReelData[CLASS_GORGON].description",
        "rationale": (
            "The target is the Gorgon class-reel description. FE8J 0x02D2 is "
            "the class-data description; 0x06BA is the reel-table duplicate."
        ),
    },
    "0x0924": {
        "source_id": "0x08E4",
        "confidence": "high",
        "subsystem": "chapter-event",
        "evidence_kind": "tutorial-executor-slot",
        "source_key": "TutEventExecType0.cursor-on-Eirika",
        "rationale": (
            "The prologue tutorial executor explicitly pairs the cursor-on-"
            "Eirika prompt with the movement prompt; FE8J 0x08E4 matches that "
            "slot, while 0x08FE is the later house-visit instruction."
        ),
    },
    "0x0925": {
        "source_id": "0x08E5",
        "confidence": "high",
        "subsystem": "chapter-event",
        "evidence_kind": "tutorial-executor-slot",
        "source_key": "TutEventExecType0.move-next-to-enemy",
        "rationale": (
            "The prologue tutorial executor's move-next-to-enemy slot matches "
            "FE8J 0x08E5; 0x08FF belongs to the Chapter 1 house tutorial."
        ),
    },
    "0x0928": {
        "source_id": "0x08E8",
        "confidence": "manual",
        "subsystem": "chapter-event",
        "evidence_kind": "prologue-tutorial-semantic-slot",
        "source_key": "EventScr_Prologue_9EF828.rapier-durability",
        "rationale": (
            "The target event explains the received rapier and durability. "
            "FE8J 0x08E8 has that exact tutorial content; 0x0900 is movement "
            "toward a village."
        ),
    },
    "0x093B": {
        "source_id": "0x08FB",
        "confidence": "manual",
        "subsystem": "chapter-event",
        "evidence_kind": "chapter-house-semantic-slot",
        "source_key": "EventScr_Ch1_Loca_Visit1.house-text",
        "rationale": (
            "The Chapter 1 house text concerns Grado occupying the castle. "
            "FE8J 0x08FB matches; 0x092C is a bandit-alarm village scene."
        ),
    },
    "0x099C": {
        "source_id": "0x095C",
        "confidence": "manual",
        "subsystem": "chapter-event",
        "evidence_kind": "chapter-event-semantic-slot",
        "source_key": "EventScr_Ch3_1.hand-axe-tutorial",
        "rationale": (
            "The Chapter 3 event is the hand-axe range tutorial. FE8J 0x095C "
            "matches; 0x093E is an unrelated victory-condition tutorial."
        ),
    },
    "0x099D": {
        "source_id": "0x095D",
        "confidence": "manual",
        "subsystem": "chapter-event",
        "evidence_kind": "chapter-event-semantic-slot",
        "source_key": "EventScr_Ch3_2.Colm-thief-tutorial",
        "rationale": (
            "The Chapter 3 event introduces Colm and thief abilities. FE8J "
            "0x095D matches; 0x0902 is terrain healing."
        ),
    },
    "0x09A2": {
        "source_id": "0x0962",
        "confidence": "manual",
        "subsystem": "chapter-event",
        "evidence_kind": "chapter-event-semantic-slot",
        "source_key": "EventScr_Ch3_7.dropped-item-tutorial",
        "rationale": (
            "The target event explains droppable green items. FE8J 0x0962 "
            "matches; 0x0948 is a weapon-shop/red-gem tutorial."
        ),
    },
    "0x0AAF": {
        "source_id": "0x0A70",
        "confidence": "high",
        "subsystem": "chapter-event",
        "evidence_kind": "shared-event-table-ordinal",
        "source_key": "Ch9B ending/source-script-3/message-4",
        "rationale": (
            "Both FE8J options are in the same parsed source script. The target "
            "is the Myrrh conversation at message ordinal 4, selecting 0x0A70 "
            "rather than ordinal 2 (0x0A6E)."
        ),
    },
    "0x0ACA": {
        "source_id": "0x0A8B",
        "confidence": "manual",
        "subsystem": "chapter-event",
        "evidence_kind": "chapter-event-semantic-slot",
        "source_key": "EventScr_Ch10B_6.fog-at-sea-house",
        "rationale": (
            "The target house discusses ocean fog and torches. FE8J 0x0A8B "
            "matches; 0x098D is a Serafew occupation conversation."
        ),
    },
    "0x0AF9": {
        "source_id": "0x0ABA",
        "confidence": "manual",
        "subsystem": "chapter-event",
        "evidence_kind": "chapter-event-semantic-slot",
        "source_key": "EventScr_Ch13B_6.Selena-village",
        "rationale": (
            "The target village discusses Selena's patrols. FE8J 0x0ABA "
            "matches; 0x092B is an unrelated handsome-mercenary village."
        ),
    },
    "0x0C52": {
        "source_id": "0x0700",
        "confidence": "high",
        "subsystem": "trainee-prep",
        "evidence_kind": "shared-call-site-slot",
        "source_key": "StartPrepErrorHelpbox.deploy-unavailable",
        "rationale": (
            "Both FE8U and FE8J preparation/unit-list functions pass this slot "
            "to StartPrepErrorHelpbox. FE8J 0x0700 is chapter deployment "
            "unavailability; 0x06F4 is the generic cannot-select message."
        ),
    },
}

_EXACT_REFERENCE_CONTEXTS: Dict[str, Dict[str, str]] = {
    "0x04F5": {
        "source_id": "0x0484",
        "source_key": "UpdateMenuItemPanel/GetStringFromIndex/argument=0/ordinal=5",
        "subsystem": "menu-definition",
    },
    "0x0505": {
        "source_id": "0x0494",
        "source_key": "GetWeaponTypeDisplayString[ITYPE_SWORD]",
        "subsystem": "menu-definition",
    },
    "0x0506": {
        "source_id": "0x0495",
        "source_key": "GetWeaponTypeDisplayString[ITYPE_LANCE]",
        "subsystem": "menu-definition",
    },
    "0x0507": {
        "source_id": "0x0496",
        "source_key": "GetWeaponTypeDisplayString[ITYPE_AXE]",
        "subsystem": "menu-definition",
    },
    "0x0508": {
        "source_id": "0x0497",
        "source_key": "GetWeaponTypeDisplayString[ITYPE_BOW]",
        "subsystem": "menu-definition",
    },
    "0x0509": {
        "source_id": "0x0498",
        "source_key": "DrawHelpBoxStaffLabels/GetStringFromIndex/argument=0/ordinal=1",
        "subsystem": "help-tutorial",
    },
}

_RAW_POINTER_PROMOTIONS: Dict[str, Dict[str, str]] = {
    "0x0032": {
        "import_id": "fe8cn.raw.import-0062",
        "symbol": "PROMO_OPTION_1_NAME",
        "text": "　第１兵種",
    },
    "0x0033": {
        "import_id": "fe8cn.raw.import-0063",
        "symbol": "PROMO_OPTION_2_NAME",
        "text": "　第２兵種",
    },
    "0x0034": {
        "import_id": "fe8cn.raw.import-0064",
        "symbol": "PROMO_OPTION_3_NAME",
        "text": "　第３兵種",
    },
}

_AUTHORED_PROMOTIONS: Dict[str, Dict[str, str]] = {
    "0x0693": {
        "translation_key": "raw_surface.unit_action.summon",
        "control_suffix": "[CTRL:001F]",
    },
    "0x0D53": {"translation_key": "framework.back", "control_suffix": ""},
    "0x0D54": {
        "translation_key": "save_compat.menu_erase_all",
        "control_suffix": "",
    },
}

_CONTEXTUAL_DUPLICATE_DONORS = {
    "0x0579": {
        "donor_target_id": "0x0145",
        "source_key": "PrepScreenProc_StartMapMenu/PREP_MAPMENU_SAVE",
        "subsystem": "menu-definition",
        "rationale": (
            "The FE8U and FE8J PrepScreenProc_StartMapMenu calls use their "
            "region's Save label in the same PREP_MAPMENU_SAVE argument slot."
        ),
    }
}

_SEMANTIC_INDEXED_CORRECTIONS: Dict[str, Dict[str, Any]] = {
    "0x002A": {
        "english_payload_sha256": "39995bb1485a5b33995817a2649b4c5867d8f4bb09d3ed286762fa66419b5590",
        "incorrect_source_id": "0x0005",
        "payload_sha256": {
            "ja": "c6f08dca315e0c2a99508ebe80bce905dfa19b2497bf522f2f2f9348e3fefbbd",
            "zh-Hans": "13fb271ef486e8bea9239079f02871f7d07f6112f69077c444501be4430fcdd9",
        },
        "rationale": (
            "The FE8U target is the World Map title. Official FE8J/FE8CN "
            "0x0564 is exactly World Map; 0x0005 is SRAM initialization."
        ),
        "source_id": "0x0564",
        "source_key": "MSG_02A/World Map title",
        "subsystem": "world-map",
    },
    "0x093F": {
        "english_payload_sha256": "de6bb8ce1d9c7dcb7d3b7bf5b822c9ce3be811177c336688f2050b5f484b336e",
        "incorrect_source_id": "0x0907",
        "payload_sha256": {
            "ja": "121ee8645ad77a4c56fd1c2f3bf73d037895d72d9dc9d37dec44dcd0c4fc3119",
            "zh-Hans": "9ca0ad0d3baf64e3d68e517398c744f0c206c0a6d599808aca94a5e0f797021d",
        },
        "rationale": (
            "The selected-unit prompt is for Eirika and the A Button. "
            "Official 0x08E4 has those semantics; 0x0907 names Gilliam."
        ),
        "source_id": "0x08E4",
        "source_key": "EventScr_Ch1Tut_EirikaVisitHouseIdle1/selected-Eirika",
        "subsystem": "chapter-event",
    },
    "0x0946": {
        "english_payload_sha256": "7f1d4ad323faf94dccf817bf0e6d933c7e1bac409f0a734b622e4ec87b87bad4",
        "incorrect_source_id": "0x090B",
        "payload_sha256": {
            "ja": "c965c719176d22ef0e60634d28ae3ca60f8844054446a5dbbb089743a24a40fa",
            "zh-Hans": "609abc4a80b7920547517ccece15b9760c2a046e228805d73e215a25f32907a6",
        },
        "rationale": (
            "The call site starts item trading by selecting Gilliam. Official "
            "0x0906 says trade, Gilliam, and A; 0x090B accepts a vulnerary."
        ),
        "source_id": "0x0906",
        "source_key": "EventScr_Ch1Tut_GilliamBattle/start-trade",
        "subsystem": "chapter-event",
    },
    "0x0947": {
        "english_payload_sha256": "5e17fead04517e766711efc5b5e79d34a3a9589e729f2a331618f3b653b13ec5",
        "incorrect_source_id": "0x090E",
        "payload_sha256": {
            "ja": "51eadbaeade1a3a92dc182e2fab0d069e5fa6658708e489d023656b5f8fdd836",
            "zh-Hans": "9c4c09e07a855e528d1547ad1f047c546db4de08fa85cd23713795a922768f2d",
        },
        "rationale": (
            "The selected-unit prompt is for Gilliam and the A Button. "
            "Official 0x0907 matches; 0x090E names Seth."
        ),
        "source_id": "0x0907",
        "source_key": "EventScr_Ch1Tut_TradeSelectGalliamIdle1/selected-Gilliam",
        "subsystem": "chapter-event",
    },
    "0x0948": {
        "english_payload_sha256": "00dcfe8fe14cc678a14184d3cb05354bcfe0ede9ee96cd95b376e094703028d9",
        "incorrect_source_id": "0x090F",
        "payload_sha256": {
            "ja": "0a76a3b2e8930411a50b61f3cc7c3b5292ae5b7a08145104d80f22f0e2125c55",
            "zh-Hans": "b1176a7fb6a9484fb5db77011e7a859cf90cf18481ddaa5de99d7ae58ce1f1d0",
        },
        "rationale": (
            "The enabled command is Trade. Official 0x0908 selects Trade; "
            "0x090F is a selected-Seth prompt."
        ),
        "source_id": "0x0908",
        "source_key": "EventScr_Ch1Tut_TradeSelectGalliamEnd/select-Trade",
        "subsystem": "chapter-event",
    },
    "0x0949": {
        "english_payload_sha256": "c479da45f5511df7173879532d3d1db00a8c0d54482b651841aceb3c72460e8f",
        "incorrect_source_id": "0x0931",
        "payload_sha256": {
            "ja": "1e1e38be578f17e137c3051c4fbc7258e739dfc49805b8e599cefa49d88c7b44",
            "zh-Hans": "418d091b5ebe0cd46b92a51a118b5bd69316886529be2775dd597da34b6ef54c",
        },
        "rationale": (
            "The trade screen shows Gilliam and Franz and requires moving "
            "right. Official 0x0909 matches; 0x0931 moves next to Ross."
        ),
        "source_id": "0x0909",
        "source_key": "EventScr_TradeTutStart/right-to-Franz-items",
        "subsystem": "chapter-event",
    },
    "0x094B": {
        "english_payload_sha256": "9e9c36da5c967877cbf85a98b145582b5e836ac68fe9e5a62cc5f62086ae4afe",
        "incorrect_source_id": "0x0932",
        "payload_sha256": {
            "ja": "e33b7e0e774714a4afad8544c8b1caf0d81803c8b4f541fd4b446b8ebcef06ae",
            "zh-Hans": "8bbbe8585f62eaba2240de766caa5c30402458af5718f36c3d6b599b9f696c43",
        },
        "rationale": (
            "The trade step accepts a vulnerary from Franz with A. Official "
            "0x090B matches; 0x0932 selects Rescue."
        ),
        "source_id": "0x090B",
        "source_key": "EventScr_TradeTut_PressAtoGetItem/accept-vulnerary",
        "subsystem": "chapter-event",
    },
    "0x094C": {
        "english_payload_sha256": "30b53ce05a829e76f88a1b4a202e9b95f629362242bb49a9610b9c7f832edacf",
        "incorrect_source_id": "0x0934",
        "payload_sha256": {
            "ja": "0455d535574d7cebcf1ff762eaaef4c0a4c3c3d72e3c2742752078d26850dc63",
            "zh-Hans": "328696022144adb29b1deb5fcc42c14d7e51ed749d914ba7c66958f461b4b5e3",
        },
        "rationale": (
            "The completed trade is closed with the B Button. Official "
            "0x090C matches; 0x0934 selects Vanessa."
        ),
        "source_id": "0x090C",
        "source_key": "EventScr_TradeTutDone/B-ends-trade",
        "subsystem": "chapter-event",
    },
    "0x094F": {
        "english_payload_sha256": "9e4929fd1b3205184102bbd7fa49c1529caceed7e4835804ea0d4add4ec454f6",
        "incorrect_source_id": "0x0939",
        "payload_sha256": {
            "ja": "cf2e81dc7a5e0069ea915e5ab9ebeeba9b37f91986c2e17c602192909971cf2d",
            "zh-Hans": "df78999d55b7413e681b951bd19328161a168da9365df48f1a5c3b0dffec1295",
        },
        "rationale": (
            "The selected-unit prompt is for Seth and the A Button. Official "
            "0x090F matches; 0x0939 names Moulder."
        ),
        "source_id": "0x090F",
        "source_key": "EventScr_Ch1Tut_SethMoveToEnemy/selected-Seth",
        "subsystem": "chapter-event",
    },
    "0x0952": {
        "english_payload_sha256": "2affa5d917e7cc50a1982a4e034cd72fb71b44a4a8da8fd83578312757a3881d",
        "incorrect_source_id": "0x090C",
        "payload_sha256": {
            "ja": "1422be7b1b97192747a1272e4de6afe439383fad7f384238a4f55d4d8de5c795",
            "zh-Hans": "f1986fe415b5340e14f29fdc53af2b9385352a6c94b78e7e9128e699fa79b954",
        },
        "rationale": (
            "The enabled command is Attack with a sword against an axe. "
            "Official 0x0912 matches; 0x090C ends a trade with B."
        ),
        "source_id": "0x0912",
        "source_key": "EventScr_Ch1Tut_AfterSethMoveToEnemy/select-Attack",
        "subsystem": "chapter-event",
    },
    "0x0971": {
        "english_payload_sha256": "f0efabb9293779ac94754c2422fe29f981467c4799000550b9bbcc2f3cee4e8b",
        "incorrect_source_id": "0x093A",
        "payload_sha256": {
            "ja": "e6da01b7737089a42522b6f70335c56fb98f0f28bf060bff1c4763df220e3b51",
            "zh-Hans": "58c4ea30ab590a8720e82b7f2fad0711fa299880ad575eaeb510326e05f89a1f",
        },
        "rationale": (
            "The movement prompt goes next to Ross and confirms with A. "
            "Official 0x0931 matches; 0x093A selects Staff."
        ),
        "source_id": "0x0931",
        "source_key": "EventScr_Ch2Tutorial1/move-next-to-Ross",
        "subsystem": "chapter-event",
    },
    "0x0972": {
        "english_payload_sha256": "0b20e9aa73c07e04526f7bcbc48663cbece24b19115f4bc6cfa21e7c83fce0ca",
        "incorrect_source_id": "0x093C",
        "payload_sha256": {
            "ja": "6bd943db2e0a14a935047a1448cf6ee6ceeb56e042d64077bb62a8adaa4d57f7",
            "zh-Hans": "31dbff1d3ff9792f8143821d9f3cb3ac1f019505b01a8fb0c71a8048d56befed",
        },
        "rationale": (
            "The enabled command is Rescue and confirms with A. Official "
            "0x0932 matches; 0x093C moves near Vanessa."
        ),
        "source_id": "0x0932",
        "source_key": "EventScr_Ch2Tutorial3/select-Rescue",
        "subsystem": "chapter-event",
    },
    "0x0975": {
        "english_payload_sha256": "4782693f037423890d22aebbf19edf1304b90e63f49bb142eaa01926e8b92c35",
        "incorrect_source_id": "0x090A",
        "payload_sha256": {
            "ja": "b8bd71817753174f3938fc9b83674e41af594adac108ef0268536586f8201a16",
            "zh-Hans": "b8cd6472c00108e29dba7e768b60effb06ab2a9660c4ed15002e32094ff25661",
        },
        "rationale": (
            "Vanessa is carrying Ross and must be selected before dropping "
            "him. Official 0x0935 matches; 0x090A selects a vulnerary."
        ),
        "source_id": "0x0935",
        "source_key": "EventScr_Ch2_5/select-Vanessa-to-drop-Ross",
        "subsystem": "chapter-event",
    },
    "0x0976": {
        "english_payload_sha256": "2159df5111a52abbdee2bf3baf9ed59f5a3f3a4667d420acddf72ebb3fa49d5d",
        "incorrect_source_id": "0x0941",
        "payload_sha256": {
            "ja": "705d93d3067d9b2fed4a88392e36cf73d1909de099c8555e99f31b199179d576",
            "zh-Hans": "618afae29260655700f6e6b609621d6457171fa09454d7cfc918bd8af795e0af",
        },
        "rationale": (
            "The enabled command is Drop and confirms with A. Official "
            "0x0936 matches; 0x0941 selects Eirika."
        ),
        "source_id": "0x0936",
        "source_key": "EventScr_Ch2Tutorial11/select-Drop",
        "subsystem": "chapter-event",
    },
    "0x097A": {
        "english_payload_sha256": "bfd346c4e6c5bc22779c094057a091ba6327e329b6373aeb6064b6b365b2b650",
        "incorrect_source_id": "0x0942",
        "payload_sha256": {
            "ja": "a532be518b7e634a3dcf1e34728db043fe7174342277af0267220e8dde95b737",
            "zh-Hans": "7738174e0316b373051d01a1c4f82a9b47ba249d77509207ad0c75cd204dea79",
        },
        "rationale": (
            "The enabled command is Staff and confirms with A. Official "
            "0x093A matches; 0x0942 moves Eirika to a village."
        ),
        "source_id": "0x093A",
        "source_key": "EventScr_Ch2Tutorial20/select-Staff",
        "subsystem": "chapter-event",
    },
    "0x097B": {
        "english_payload_sha256": "bda961f5ccaa106a93197835de48c4aad377fe27652c07077dd8edd4581d2812",
        "incorrect_source_id": "0x0912",
        "payload_sha256": {
            "ja": "046443cb415265d21803b0a1f5bade8b820780aefb406449673e00b9ea378b4c",
            "zh-Hans": "fc8545b5c526579b4095ae5a696f49ea688fa0980f676d13b5f5f5e79787b94d",
        },
        "rationale": (
            "Ross has been rescued; the next unit is Moulder moving toward "
            "Vanessa. Official 0x093B matches; 0x0912 is an attack prompt."
        ),
        "source_id": "0x093B",
        "source_key": "EventScr_Ch2_4/select-Moulder-after-rescue",
        "subsystem": "chapter-event",
    },
    "0x097C": {
        "english_payload_sha256": "ef5dfb3f37dff6e67df264b9a0231a29d0eb545e73c543b827ac76ff5784d7f5",
        "incorrect_source_id": "0x0943",
        "payload_sha256": {
            "ja": "a9b701b7debabf3798bcc1f2146debdb8ea2d3233df92e72feeb2cb7e3d8c5a7",
            "zh-Hans": "58150df0db037fbb172b83270e434a0f2f8e86aaaedd4f63fe6428d89338b2dc",
        },
        "rationale": (
            "The movement prompt goes to the flashing space near Vanessa and "
            "confirms with A. Official 0x093C matches; 0x0943 selects Visit."
        ),
        "source_id": "0x093C",
        "source_key": "EventScr_Ch2Tutorial8/move-near-Vanessa",
        "subsystem": "chapter-event",
    },
    "0x0982": {
        "english_payload_sha256": "0fe1fc32aa79d2760e650f7cab077435e7490b72dd12adc26e28f08abf2f4954",
        "incorrect_source_id": "0x0947",
        "payload_sha256": {
            "ja": "d5135d4aa50dfd941dfb3d15d2a142a432b84adf8791a1931abc23fae46536d9",
            "zh-Hans": "243067d1d1172c1b3a8f84b26cb0aadcd86b8721eb2fff93c1e3526fe0fa397c",
        },
        "rationale": (
            "The movement prompt sends Eirika to the flashing village space "
            "and confirms with A. Official 0x0942 matches; 0x0947 selects Talk."
        ),
        "source_id": "0x0942",
        "source_key": "EventScr_Ch2Tutorial23/move-Eirika-to-village",
        "subsystem": "chapter-event",
    },
}


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _sha256_text(text: str) -> str:
    return _sha256_bytes(text.encode("utf-8"))


def _json_hash(data: Any) -> str:
    return _sha256_bytes(
        json.dumps(
            data, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode("utf-8")
    )


def _indexed_payloads(data: str, source_name: str) -> Dict[int, str]:
    return {
        message.id: message.text
        for message in parse_hash_indexed(data, source_name=source_name)
    }


def _raw_payloads(data: Any) -> Dict[str, str]:
    if not isinstance(data, dict) or not isinstance(data.get("records"), list):
        raise FinalMappingError("zh-Hans raw source is malformed")
    return {
        row["import_id"]: row["text"]
        for row in data["records"]
        if isinstance(row, dict)
    }


def _catalog_strings(data: Any, locale: str) -> Dict[str, str]:
    if not isinstance(data, dict) or data.get("locale") != locale:
        raise FinalMappingError(f"{locale} authored catalog is malformed")
    strings = data.get("strings")
    if not isinstance(strings, dict):
        raise FinalMappingError(f"{locale} authored catalog strings are malformed")
    return strings


def _runtime_authored_strings(data: Any, locale: str) -> Dict[str, str]:
    if (
        not isinstance(data, dict)
        or data.get("kind") != "fe8u-game-authored-catalog"
        or data.get("locale") != locale
        or data.get("schema_version") != 1
    ):
        raise FinalMappingError(f"{locale} runtime authored catalog is malformed")
    strings = data.get("strings")
    if not isinstance(strings, dict) or data.get("target_count") != len(strings):
        raise FinalMappingError(
            f"{locale} runtime authored catalog strings are malformed"
        )
    return strings


def _original_row(row: Mapping[str, Any]) -> Dict[str, Any]:
    promotion = row.get("verification", {}).get("promotion", {})
    if promotion.get("pipeline") != FINAL_MAPPING_PIPELINE:
        return deepcopy(dict(row))
    original_source = promotion.get("original_source")
    original_verification = promotion.get("original_verification")
    if not isinstance(original_source, dict) or not isinstance(
        original_verification, dict
    ):
        raise FinalMappingError(
            f"{row.get('target_id')}: final promotion lacks original row state"
        )
    restored = deepcopy(dict(row))
    restored["source"] = deepcopy(original_source)
    restored["verification"] = deepcopy(original_verification)
    return restored


def recover_original_rows(mapping_data: Mapping[str, Any]) -> List[Dict[str, Any]]:
    rows = mapping_data.get("rows")
    if not isinstance(rows, list):
        raise FinalMappingError("mapping rows are malformed")
    return [_original_row(row) for row in rows]


def _promotion_verification(
    *,
    original: Mapping[str, Any],
    precedence: str,
    confidence: str,
    evidence: str,
    evidence_kind: str,
    source_table: str,
    source_symbol: str,
    source_key: str,
    subsystem: str,
    rationale: str,
    details: Mapping[str, Any],
) -> Dict[str, Any]:
    return {
        "confidence": confidence,
        "evidence": evidence,
        "evidence_kind": evidence_kind,
        "method": "deterministic-final-mapping-promotion",
        "promotion": {
            "details": deepcopy(dict(details)),
            "original_source": deepcopy(original["source"]),
            "original_verification": deepcopy(original["verification"]),
            "pipeline": FINAL_MAPPING_PIPELINE,
            "precedence": precedence,
        },
        "rationale": rationale,
        "source_key": source_key,
        "source_symbol": source_symbol,
        "source_table": source_table,
        "subsystem": subsystem,
    }


def _promote(
    row: Dict[str, Any],
    source: Mapping[str, Any],
    verification: Mapping[str, Any],
) -> None:
    row["source"] = deepcopy(dict(source))
    row["verification"] = deepcopy(dict(verification))


def _candidate_source_id(candidate: Mapping[str, Any]) -> Optional[str]:
    source = candidate.get("source", {})
    return source.get("id") if source.get("kind") == "indexed" else None


def _validate_indexed_candidate(
    candidate: Mapping[str, Any],
    indexed: Mapping[str, Mapping[int, str]],
) -> Dict[str, str]:
    if candidate.get("row_type") != "indexed":
        raise FinalMappingError("FEBuilder candidate is not an indexed provider")
    source = candidate.get("source", {})
    source_id = source.get("id")
    if not isinstance(source_id, str):
        raise FinalMappingError("FEBuilder indexed candidate has no source ID")
    numeric = int(source_id, 16)
    for locale in ("ja", "zh-Hans"):
        text = indexed[locale].get(numeric)
        payload = candidate.get("payloads", {}).get(locale, {})
        if text is None or payload.get("sha256") != _sha256_text(text):
            raise FinalMappingError(
                f"{source_id}: FEBuilder {locale} payload hash is invalid"
            )
    return _indexed_source(source_id)


def _provider_payloads(
    *,
    target_id: int,
    source: Mapping[str, Any],
    indexed: Mapping[str, Mapping[int, str]],
    raw_payloads: Mapping[str, str],
    ja_raw_providers: Mapping[int, Any],
    authored: Mapping[str, Mapping[str, str]],
) -> Tuple[bytes, bytes]:
    kind = source["kind"]
    if kind == "indexed":
        source_id = int(source["id"], 16)
        return tuple(
            encode_canonical_text(indexed[locale][source_id])
            for locale in ("ja", "zh-Hans")
        )  # type: ignore[return-value]
    if kind == "raw":
        ja_source = source.get("regional_sources", {}).get("ja", {})
        ja_text = resolve_ja_raw_text(
            target_id=target_id,
            ja_source=ja_source,
            providers=ja_raw_providers,
        )
        return (
            encode_canonical_text(ja_text),
            encode_canonical_text(raw_payloads[source["import_id"]]),
        )
    if kind == "authored":
        key = source["translation_key"]
        suffix = source.get("control_suffix", "")
        return tuple(
            encode_canonical_text(authored[locale][key] + suffix)
            for locale in ("ja", "zh-Hans")
        )  # type: ignore[return-value]
    raise FinalMappingError(f"cannot resolve provider payload for {kind!r}")


def _source_preference(source: Mapping[str, Any]) -> int:
    return {"indexed": 0, "raw": 1, "authored": 2}[source["kind"]]


def _raw_source_for_pointer(
    target_id: str,
    decision: Mapping[str, str],
    *,
    febuilder_row: Mapping[str, Any],
    ja_raw_providers: Mapping[int, Any],
    raw_payloads: Mapping[str, str],
    repo_root: Path,
) -> Dict[str, Any]:
    candidates = febuilder_row.get("candidates", [])
    if (
        febuilder_row.get("marks") != ["unique-uncontested"]
        or len(candidates) != 1
        or candidates[0].get("row_type") != "pointer"
        or candidates[0].get("source", {}).get("raw_import_id")
        != decision["import_id"]
    ):
        raise FinalMappingError(
            f"{target_id}: raw pointer promotion is not unique and uncontested"
        )
    provider = ja_raw_providers.get(int(target_id, 16))
    if (
        provider is None
        or provider.symbol != decision["symbol"]
        or provider.text != decision["text"]
    ):
        raise FinalMappingError(
            f"{target_id}: Japanese raw provider does not match reviewed literal"
        )
    source_path = repo_root / "src/classchg-menuselect.c"
    source_text = source_path.read_text(encoding="utf-8")
    literals = re.findall(
        rf'#define\s+{re.escape(decision["symbol"])}\s+"([^"]*)"', source_text
    )
    if decision["text"] not in literals:
        raise FinalMappingError(
            f"{target_id}: reviewed Japanese macro literal changed"
        )
    if not raw_payloads.get(decision["import_id"]):
        raise FinalMappingError(
            f"{target_id}: Simplified Chinese raw payload is unavailable"
        )
    return {
        "import_id": decision["import_id"],
        "kind": "raw",
        "regional_sources": {
            "ja": {"kind": "symbol", "symbol": decision["symbol"]},
            "zh-Hans": {
                "import_id": decision["import_id"],
                "kind": "import",
            },
        },
    }


def _authored_source(
    target_id: str,
    decision: Mapping[str, str],
    *,
    english_entry: Any,
    authored: Mapping[str, Mapping[str, str]],
    runtime_authored: Mapping[str, Mapping[str, str]],
) -> Dict[str, Any]:
    key = decision["translation_key"]
    suffix = decision["control_suffix"]
    for locale in ("ja", "zh-Hans"):
        if not authored[locale].get(key):
            raise FinalMappingError(
                f"{target_id}: existing {locale} expansion translation {key!r} is missing"
            )
    english_catalog = authored["en"]
    if key not in english_catalog:
        raise FinalMappingError(
            f"{target_id}: English expansion source {key!r} is missing"
        )
    if encode_canonical_text(english_catalog[key] + suffix) != english_entry.encoded_bytes:
        raise FinalMappingError(
            f"{target_id}: expansion English text/control payload is not exact"
        )
    for locale in ("ja", "zh-Hans"):
        if key not in runtime_authored[locale]:
            raise FinalMappingError(
                f"{target_id}: runtime authored translation {key!r} is missing"
            )
        if encode_canonical_text(
            runtime_authored[locale][key] + suffix
        ) != encode_canonical_text(authored[locale][key] + suffix):
            raise FinalMappingError(
                f"{target_id}: runtime authored translation {key!r} changed payload"
            )
    source: Dict[str, Any] = {"kind": "authored", "translation_key": key}
    if suffix:
        source["control_suffix"] = suffix
    return source


def _canonical_english(entry: Any, definitions: Mapping[str, Sequence[int]]) -> Dict[str, Any]:
    source = _C_COMMENT_RE.sub("", entry.source_text).replace("\r", "").replace(
        "\n", ""
    )
    text_parts: List[str] = []
    controls = []
    placeholders = []
    position = 0
    control_ordinal = 0
    placeholder_ordinal = 0
    for match in _TOKEN_RE.finditer(source):
        text_parts.append(source[position : match.start()])
        name = match.group(1)
        position = match.end()
        if name == "X":
            continue
        if name in _PRINTABLE_TOKENS:
            text_parts.append(_PRINTABLE_TOKENS[name])
            continue
        values = definitions.get(name)
        if values is None:
            raise FinalMappingError(
                f"{format_message_id(entry.target_id)}: unknown English token [{name}]"
            )
        record = {
            "bytes_hex": bytes(values).hex().upper(),
            "name": name,
            "token": f"[{name}]",
        }
        if name in _PLACEHOLDER_TOKENS:
            placeholder_ordinal += 1
            record["ordinal"] = placeholder_ordinal
            placeholders.append(record)
            text_parts.append("{" + name + "}")
        else:
            control_ordinal += 1
            record["ordinal"] = control_ordinal
            controls.append(record)
    text_parts.append(source[position:])
    return {
        "controls": controls,
        "english_canonical_text": "".join(text_parts),
        "english_payload_sha256": _sha256_bytes(entry.encoded_bytes),
        "placeholders": placeholders,
        "source_text": entry.source_text,
    }


def _suggested_key(subsystem: str, entry: Any) -> str:
    suffix = entry.definition or f"msg_{entry.target_id:04x}"
    suffix = _IDENTIFIER_RE.sub("_", suffix.lower()).strip("_")
    family = _IDENTIFIER_RE.sub("_", subsystem.lower()).strip("_")
    return f"game.{family}.{suffix}"


def _deduplicate_sites(sites: Iterable[Mapping[str, Any]]) -> List[Dict[str, Any]]:
    unique = {}
    for raw_site in sites:
        site = deepcopy(dict(raw_site))
        key = (
            site.get("kind"),
            site.get("path"),
            site.get("line"),
            site.get("slot"),
        )
        unique[key] = site
    return [
        unique[key]
        for key in sorted(
            unique,
            key=lambda value: tuple("" if item is None else str(item) for item in value),
        )
    ]


def _queue_reason(
    *,
    target_id: str,
    original: Mapping[str, Any],
    structural_residual: Optional[Mapping[str, Any]],
    donor_payload_variants: int,
) -> Tuple[str, str]:
    reason = original["source"]["reason"]
    if donor_payload_variants > 1:
        return (
            "duplicate-english-provider-ambiguity",
            "Exact English payload matches mapped targets whose JA/ZH payloads "
            "differ; context does not select one provider safely.",
        )
    if reason != "not-yet-verified":
        return (
            reason,
            f"The target is explicitly classified {reason!r} and no authorized "
            "regional source or allowed authored translation exists.",
        )
    if structural_residual is not None:
        structural_reason = structural_residual["reason"]
        return (
            structural_reason,
            "Structural/FEBuilder review left this target without an independent "
            f"provider: {structural_reason}.",
        )
    return (
        "no-independent-provider",
        "No structural, FEBuilder, raw, authored, or exact-English provider "
        "satisfies the final promotion policy.",
    )


def build_final_mapping_artifacts(
    *,
    repo_root: Path,
    target_count: int,
    mapping_data: Any,
    febuilder_data: Any,
    structural_data: Any,
    english_texts_path: Path,
    english_definitions_path: Path,
    ja_indexed_text: str,
    zh_indexed_text: str,
    zh_raw_data: Any,
    ja_raw_data: Any,
    authored_catalogs: Mapping[str, Any],
    runtime_authored_catalogs: Mapping[str, Any],
    authored_queue_data: Any,
) -> Dict[str, Dict[str, Any]]:
    """Promote only independently supported providers, then queue every residual."""

    repo_root = Path(repo_root)
    current = validate_mapping_document(
        mapping_data, target_count=target_count, repo_root=repo_root
    )
    if not current.coverage_eligible or len(current.rows) != target_count:
        raise FinalMappingError("final promotion requires the complete authoritative map")
    validate_febuilder_evidence_document(febuilder_data, target_count=target_count)
    validate_structural_completion_evidence(
        structural_data, repo_root=repo_root, target_count=target_count
    )

    english_entries = load_english_source_entries(
        english_texts_path, english_definitions_path, target_count=target_count
    )
    definitions = load_english_definitions(english_definitions_path)
    indexed = {
        "ja": _indexed_payloads(ja_indexed_text, "ja indexed"),
        "zh-Hans": _indexed_payloads(zh_indexed_text, "zh-Hans indexed"),
    }
    raw_payloads = _raw_payloads(zh_raw_data)
    ja_raw_providers = load_ja_raw_providers(ja_raw_data)
    authored = {
        locale: _catalog_strings(authored_catalogs[locale], locale)
        for locale in ("en", "ja", "zh-Hans")
    }
    runtime_authored = {
        locale: _runtime_authored_strings(runtime_authored_catalogs[locale], locale)
        for locale in ("ja", "zh-Hans")
    }
    provider_authored = {
        locale: {**authored[locale], **runtime_authored[locale]}
        for locale in ("ja", "zh-Hans")
    }
    if not isinstance(authored_queue_data, dict) or not isinstance(
        authored_queue_data.get("targets"), list
    ):
        raise FinalMappingError("historical authored queue is malformed")

    original_rows = recover_original_rows(mapping_data)
    rows = deepcopy(original_rows)
    row_by_id = {row["target_id"]: row for row in rows}
    original_by_id = {row["target_id"]: row for row in original_rows}
    febuilder_by_id = {
        row["target_id"]: row for row in febuilder_data["targets"]
    }
    proposal_by_id = {
        row["target_id"]: row for row in structural_data["proposals"]
    }
    collision_by_id = {
        row["target_id"]: row for row in structural_data["collisions"]
    }
    residual_by_id = {
        row["target_id"]: row for row in structural_data["residual_targets"]
    }

    protected = {
        target_id: _json_hash(row["source"])
        for target_id, row in original_by_id.items()
        if row["source"]["kind"] != "english_fallback"
    }
    promoted: Dict[str, str] = {}

    def is_research_fallback(target_id: str) -> bool:
        return original_by_id[target_id]["source"] == {
            "kind": "english_fallback",
            "reason": "not-yet-verified",
        }

    for target_id, proposal in sorted(proposal_by_id.items()):
        if not is_research_fallback(target_id) or proposal["confidence"] != "high":
            continue
        if target_id in collision_by_id:
            raise FinalMappingError(
                f"{target_id}: high structural proposal is also a collision"
            )
        source = _indexed_source(proposal["source_id"])
        for locale in ("ja", "zh-Hans"):
            if not indexed[locale].get(int(proposal["source_id"], 16)):
                raise FinalMappingError(
                    f"{target_id}: structural source payload is empty in {locale}"
                )
        verification = _promotion_verification(
            original=original_by_id[target_id],
            precedence="b-structural-high",
            confidence="high",
            evidence=(
                f"{proposal['evidence']['basis']}: "
                f"{proposal['semantic_slot']['key']}"
            ),
            evidence_kind=proposal["evidence"]["basis"],
            source_table=proposal["semantic_slot"]["family"],
            source_symbol=proposal["semantic_slot"]["key"],
            source_key=proposal["semantic_slot"]["key"],
            subsystem=proposal["semantic_slot"]["family"],
            rationale="Parsed named/keyed source and target structures prove this slot.",
            details={"structural_source_id": proposal["source_id"]},
        )
        _promote(row_by_id[target_id], source, verification)
        promoted[target_id] = "b-structural-high"

    eligible_fb: Dict[str, Tuple[Mapping[str, Any], Dict[str, str]]] = {}
    source_targets: Dict[str, List[str]] = defaultdict(list)
    for target_id, evidence in sorted(febuilder_by_id.items()):
        if not is_research_fallback(target_id) or target_id in promoted:
            continue
        if (
            "conflicts" in evidence["marks"]
            or "collision-needs-context" in evidence["marks"]
        ):
            continue
        if target_id in collision_by_id:
            continue
        candidates = evidence["candidates"]
        if len(candidates) != 1 or candidates[0].get("row_type") != "indexed":
            continue
        if not (
            "unique-uncontested" in evidence["marks"]
            or "agrees-with-structural" in evidence["marks"]
        ):
            continue
        source = _validate_indexed_candidate(candidates[0], indexed)
        source_key = json.dumps(source, sort_keys=True)
        eligible_fb[target_id] = (evidence, source)
        source_targets[source_key].append(target_id)
    source_collisions = {
        source: targets for source, targets in source_targets.items() if len(targets) > 1
    }
    if source_collisions:
        raise FinalMappingError(
            "FEBuilder source-to-target collisions remain: "
            + json.dumps(source_collisions, sort_keys=True)
        )
    for target_id, (evidence, source) in sorted(eligible_fb.items()):
        proposal = proposal_by_id.get(target_id)
        structural_agreement = (
            proposal is not None and proposal["source_id"] == source["id"]
        )
        verification = _promotion_verification(
            original=original_by_id[target_id],
            precedence="c-febuilder",
            confidence="high",
            evidence=(
                f"FEBuilder line {evidence['candidates'][0]['source_line']} "
                f"maps {source['id']} to {target_id}"
            ),
            evidence_kind=(
                "febuilder-structural-agreement"
                if structural_agreement
                else "febuilder-unique-uncontested"
            ),
            source_table="FEBuilder translate_textid_FE8",
            source_symbol="translate_textid_FE8.txt",
            source_key=f"{source['id']}->{target_id}",
            subsystem=(
                proposal["semantic_slot"]["family"]
                if proposal is not None
                else original_by_id[target_id]["verification"]["subsystem"]
            ),
            rationale=(
                "Independent structural evidence agrees with the FEBuilder provider."
                if structural_agreement
                else "The FEBuilder target and source are unique, payload-valid, "
                "and have no target/source collision."
            ),
            details={
                "candidate_payloads": evidence["candidates"][0]["payloads"],
                "structural_agreement": structural_agreement,
            },
        )
        _promote(row_by_id[target_id], source, verification)
        promoted[target_id] = "c-febuilder"

    for target_id, decision in sorted(_RAW_POINTER_PROMOTIONS.items()):
        if not is_research_fallback(target_id) or target_id in promoted:
            raise FinalMappingError(f"{target_id}: raw pointer promotion precondition changed")
        source = _raw_source_for_pointer(
            target_id,
            decision,
            febuilder_row=febuilder_by_id[target_id],
            ja_raw_providers=ja_raw_providers,
            raw_payloads=raw_payloads,
            repo_root=repo_root,
        )
        verification = _promotion_verification(
            original=original_by_id[target_id],
            precedence="c-febuilder-raw",
            confidence="high",
            evidence=(
                f"FEBuilder pointer resolves {decision['import_id']} and tracked "
                f"{decision['symbol']} supplies exact Japanese text"
            ),
            evidence_kind="febuilder-pointer-with-tracked-ja-literal",
            source_table="class-change option initializer",
            source_symbol=decision["symbol"],
            source_key=target_id,
            subsystem="trainee-prep",
            rationale=(
                "The pointer candidate is unique; Simplified Chinese is the exact "
                "raw import and Japanese is the tracked legacy macro literal."
            ),
            details={"import_id": decision["import_id"]},
        )
        _promote(row_by_id[target_id], source, verification)
        promoted[target_id] = "c-febuilder-raw"

    for target_id, decision in sorted(_EXACT_REFERENCE_CONTEXTS.items()):
        if not is_research_fallback(target_id) or target_id in promoted:
            raise FinalMappingError(
                f"{target_id}: exact reference promotion precondition changed"
            )
        proposal = proposal_by_id.get(target_id)
        if proposal is None or proposal["source_id"] != decision["source_id"]:
            raise FinalMappingError(
                f"{target_id}: exact reference source no longer matches evidence"
            )
        source = _indexed_source(decision["source_id"])
        verification = _promotion_verification(
            original=original_by_id[target_id],
            precedence="d-structural-reference-second-check",
            confidence="high",
            evidence=f"Exact named table/call key: {decision['source_key']}",
            evidence_kind="exact-table-or-call-key",
            source_table=decision["source_key"].split("/", 1)[0],
            source_symbol=decision["source_key"].split("/", 1)[0],
            source_key=decision["source_key"],
            subsystem=decision["subsystem"],
            rationale=(
                "The authorized reference pair is independently confirmed by "
                "the same named table index or message-consuming call slot."
            ),
            details={"structural_source_id": decision["source_id"]},
        )
        _promote(row_by_id[target_id], source, verification)
        promoted[target_id] = "d-structural-reference-second-check"

    for target_id, decision in sorted(_CONTEXTUAL_COLLISION_RESOLUTIONS.items()):
        if not is_research_fallback(target_id) or target_id in promoted:
            raise FinalMappingError(
                f"{target_id}: contextual resolution precondition changed"
            )
        accepted = decision["source_id"]
        option_ids = set()
        if target_id in collision_by_id:
            option_ids.update(
                option["source_id"]
                for option in collision_by_id[target_id]["source_options"]
            )
        if target_id in febuilder_by_id:
            option_ids.update(
                source_id
                for candidate in febuilder_by_id[target_id]["candidates"]
                if (source_id := _candidate_source_id(candidate)) is not None
            )
        if accepted not in option_ids:
            raise FinalMappingError(
                f"{target_id}: reviewed source {accepted} is no longer a candidate"
            )
        if not indexed["ja"].get(int(accepted, 16)) or not indexed["zh-Hans"].get(
            int(accepted, 16)
        ):
            raise FinalMappingError(
                f"{target_id}: reviewed collision provider payload is empty"
            )
        verification = _promotion_verification(
            original=original_by_id[target_id],
            precedence="d-contextual-resolution",
            confidence=decision["confidence"],
            evidence=decision["rationale"],
            evidence_kind=decision["evidence_kind"],
            source_table=decision["source_key"].split(".", 1)[0],
            source_symbol=decision["source_key"].split(".", 1)[0],
            source_key=decision["source_key"],
            subsystem=decision["subsystem"],
            rationale=decision["rationale"],
            details={
                "accepted_source_id": accepted,
                "reviewed_source_options": sorted(option_ids),
            },
        )
        _promote(row_by_id[target_id], _indexed_source(accepted), verification)
        promoted[target_id] = "d-contextual-resolution"

    for target_id, decision in sorted(_SEMANTIC_INDEXED_CORRECTIONS.items()):
        target = int(target_id, 16)
        incorrect_source = _indexed_source(decision["incorrect_source_id"])
        if row_by_id[target_id]["source"] != incorrect_source:
            raise FinalMappingError(
                f"{target_id}: semantic correction no longer replaces "
                f"{decision['incorrect_source_id']}"
            )
        if (
            _sha256_bytes(english_entries[target].encoded_bytes)
            != decision["english_payload_sha256"]
        ):
            raise FinalMappingError(
                f"{target_id}: semantic correction English payload changed"
            )
        source = _indexed_source(decision["source_id"])
        source_numeric = int(decision["source_id"], 16)
        for locale in ("ja", "zh-Hans"):
            payload = indexed[locale].get(source_numeric)
            if (
                payload is None
                or _sha256_text(payload)
                != decision["payload_sha256"][locale]
            ):
                raise FinalMappingError(
                    f"{target_id}: semantic correction {locale} payload changed"
                )
        verification = _promotion_verification(
            original=original_by_id[target_id],
            precedence="d-semantic-correction",
            confidence="manual",
            evidence=decision["rationale"],
            evidence_kind="official-regional-semantic-payload",
            source_table="FE8J/FE8CN indexed message corpus",
            source_symbol=decision["source_id"],
            source_key=decision["source_key"],
            subsystem=decision["subsystem"],
            rationale=decision["rationale"],
            details={
                "english_payload_sha256": decision["english_payload_sha256"],
                "incorrect_source": incorrect_source,
                "payload_sha256": decision["payload_sha256"],
                "replacement_source_id": decision["source_id"],
            },
        )
        _promote(row_by_id[target_id], source, verification)
        promoted[target_id] = "d-semantic-correction"

    for target_id, decision in sorted(_AUTHORED_PROMOTIONS.items()):
        original = original_by_id[target_id]
        if original["source"]["kind"] != "english_fallback" or target_id in promoted:
            raise FinalMappingError(
                f"{target_id}: authored promotion precondition changed"
            )
        source = _authored_source(
            target_id,
            decision,
            english_entry=english_entries[int(target_id, 16)],
            authored=authored,
            runtime_authored=runtime_authored,
        )
        verification = _promotion_verification(
            original=original,
            precedence="d-existing-authored",
            confidence="explicit",
            evidence=(
                f"Existing expansion translation {decision['translation_key']} "
                "matches the FE8U English/control payload"
            ),
            evidence_kind="existing-expansion-translation",
            source_table="texts/expansion/catalog.<locale>.json",
            source_symbol=decision["translation_key"],
            source_key=decision["translation_key"],
            subsystem=original["verification"]["subsystem"],
            rationale="Reuses an existing reviewed expansion translation; no new text is authored.",
            details={"control_suffix": decision["control_suffix"]},
        )
        _promote(row_by_id[target_id], source, verification)
        promoted[target_id] = "d-existing-authored"

    for target_id, decision in sorted(_CONTEXTUAL_DUPLICATE_DONORS.items()):
        if not is_research_fallback(target_id) or target_id in promoted:
            raise FinalMappingError(
                f"{target_id}: contextual duplicate precondition changed"
            )
        donor_id = decision["donor_target_id"]
        donor = row_by_id[donor_id]
        if donor["source"]["kind"] == "english_fallback":
            raise FinalMappingError(f"{target_id}: duplicate donor is not mapped")
        if (
            english_entries[int(target_id, 16)].encoded_bytes
            != english_entries[int(donor_id, 16)].encoded_bytes
        ):
            raise FinalMappingError(f"{target_id}: duplicate donor English differs")
        source = deepcopy(donor["source"])
        if source["kind"] == "raw":
            source["regional_sources"]["ja"]["provider_target_id"] = donor_id
        verification = _promotion_verification(
            original=original_by_id[target_id],
            precedence="e-exact-english-context",
            confidence="high",
            evidence=decision["rationale"],
            evidence_kind="exact-english-plus-call-slot",
            source_table=decision["source_key"].split("/", 1)[0],
            source_symbol=decision["source_key"].split("/", 1)[0],
            source_key=decision["source_key"],
            subsystem=decision["subsystem"],
            rationale=decision["rationale"],
            details={"donor_target_id": donor_id},
        )
        _promote(row_by_id[target_id], source, verification)
        promoted[target_id] = "e-exact-english-context"

    donors_by_english: Dict[bytes, List[Tuple[str, Mapping[str, Any], Tuple[bytes, bytes]]]] = defaultdict(list)
    for target_id, row in sorted(row_by_id.items()):
        if row["source"]["kind"] == "english_fallback":
            continue
        payloads = _provider_payloads(
            target_id=int(target_id, 16),
            source=row["source"],
            indexed=indexed,
            raw_payloads=raw_payloads,
            ja_raw_providers=ja_raw_providers,
            authored=provider_authored,
        )
        donors_by_english[english_entries[int(target_id, 16)].encoded_bytes].append(
            (target_id, row["source"], payloads)
        )

    donor_variants: Dict[str, int] = {}
    for target_id, row in sorted(row_by_id.items()):
        if row["source"]["kind"] != "english_fallback":
            continue
        donors = donors_by_english.get(
            english_entries[int(target_id, 16)].encoded_bytes, []
        )
        localized_variants = {donor[2] for donor in donors}
        donor_variants[target_id] = len(localized_variants)
        if len(localized_variants) != 1:
            continue
        donor_id, donor_source, donor_payloads = min(
            donors,
            key=lambda donor: (
                _source_preference(donor[1]),
                int(donor[0], 16),
            ),
        )
        source = deepcopy(dict(donor_source))
        if source["kind"] == "raw":
            source["regional_sources"]["ja"]["provider_target_id"] = (
                source["regional_sources"]["ja"].get(
                    "provider_target_id", donor_id
                )
            )
        if (
            _provider_payloads(
                target_id=int(target_id, 16),
                source=source,
                indexed=indexed,
                raw_payloads=raw_payloads,
                ja_raw_providers=ja_raw_providers,
                authored=provider_authored,
            )
            != donor_payloads
        ):
            raise FinalMappingError(
                f"{target_id}: reused provider payload differs from donor {donor_id}"
            )
        verification = _promotion_verification(
            original=original_by_id[target_id],
            precedence="e-exact-english",
            confidence="high",
            evidence=(
                f"Exact normalized English payload/control equality with {donor_id}; "
                "all mapped donors have identical JA/ZH payloads"
            ),
            evidence_kind="exact-normalized-english-and-control-equality",
            source_table="FE8U English message corpus",
            source_symbol=english_entries[int(target_id, 16)].definition
            or target_id,
            source_key=_sha256_bytes(
                english_entries[int(target_id, 16)].encoded_bytes
            ),
            subsystem=original_by_id[target_id]["verification"]["subsystem"],
            rationale="Reuses a mapped target's identical regional provider.",
            details={
                "donor_target_id": donor_id,
                "donor_target_ids": sorted(donor[0] for donor in donors),
                "localized_payload_sha256": {
                    "ja": _sha256_bytes(donor_payloads[0]),
                    "zh-Hans": _sha256_bytes(donor_payloads[1]),
                },
            },
        )
        _promote(row, source, verification)
        promoted[target_id] = "e-exact-english"

    interim_mapping = {
        "authoritative": True,
        "authority": "verified",
        "kind": "fe8u-locale-mapping",
        "locale_ids": ["ja", "zh-Hans"],
        "note": (
            "Authoritative FE8U decisions after deterministic evidence promotion. "
            "Every promoted row records its precedence and recoverable original "
            "fallback; remaining rows are the authored-translation queue."
        ),
        "rows": deepcopy(
            [
                row_by_id[format_message_id(target)]
                for target in range(target_count)
            ]
        ),
        "schema_version": 2,
    }
    source_map_sha256 = authored_queue_data.get("authoritative_target_map_sha256")
    current_interim_map_sha256 = _json_hash(interim_mapping)
    queue_targets = authored_queue_data["targets"]
    queue_by_id = {
        row["target_id"]: row
        for row in queue_targets
        if isinstance(row, dict) and isinstance(row.get("target_id"), str)
    }
    if len(queue_by_id) != len(queue_targets):
        raise FinalMappingError("historical authored queue target IDs are malformed")
    remaining_fallback_ids = {
        target_id
        for target_id, row in row_by_id.items()
        if row["source"]["kind"] == "english_fallback"
    }
    if set(queue_by_id) != remaining_fallback_ids:
        raise FinalMappingError(
            "historical authored queue does not equal the pre-authored fallback set"
        )
    expected_runtime_keys = {
        row["suggested_key"] for row in queue_targets
    } | {
        decision["translation_key"] for decision in _AUTHORED_PROMOTIONS.values()
    }
    for locale in ("ja", "zh-Hans"):
        if set(runtime_authored[locale]) != expected_runtime_keys:
            raise FinalMappingError(
                f"{locale}: runtime authored catalog has missing or extra keys"
            )

    for target_id in sorted(queue_by_id):
        queue_row = queue_by_id[target_id]
        key = queue_row.get("suggested_key")
        if not isinstance(key, str) or not key:
            raise FinalMappingError(f"{target_id}: authored queue key is malformed")
        english_payload_sha256 = queue_row.get("english_payload_sha256")
        if english_payload_sha256 != _sha256_bytes(
            english_entries[int(target_id, 16)].encoded_bytes
        ):
            raise FinalMappingError(
                f"{target_id}: authored queue English payload hash drifted"
            )
        for locale in ("ja", "zh-Hans"):
            if not runtime_authored[locale].get(key):
                raise FinalMappingError(
                    f"{target_id}: {locale} authored translation {key!r} is missing"
                )
        source = {"kind": "authored", "translation_key": key}
        verification = _promotion_verification(
            original=original_by_id[target_id],
            precedence="f-authored-queue",
            confidence="explicit",
            evidence=(
                f"Reviewed JA/ZH authored shard payloads fulfill queue key {key}"
            ),
            evidence_kind="reviewed-authored-translation-shard",
            source_table="texts/locales/authored/catalog.<locale>.json",
            source_symbol=key,
            source_key=key,
            subsystem=queue_row["subsystem"],
            rationale=(
                "Promotes one exact historical queue target after deterministic "
                "shard validation and locale-parity checks."
            ),
            details={
                "english_payload_sha256": english_payload_sha256,
                "source_queue_sha256": _sha256_bytes(
                    canonical_json_bytes(authored_queue_data)
                ),
            },
        )
        _promote(row_by_id[target_id], source, verification)
        promoted[target_id] = "f-authored-queue"

    for target_id, expected_hash in protected.items():
        if _json_hash(row_by_id[target_id]["source"]) != expected_hash:
            raise FinalMappingError(
                f"{target_id}: existing verified provider was changed"
            )

    final_mapping = {
        "authoritative": True,
        "authority": "verified",
        "kind": "fe8u-locale-mapping",
        "locale_ids": ["ja", "zh-Hans"],
        "note": (
            "Authoritative FE8U decisions after deterministic evidence and "
            "reviewed authored-shard promotion. Every promoted row records its "
            "precedence and recoverable original source."
        ),
        "rows": [row_by_id[format_message_id(target)] for target in range(target_count)],
        "schema_version": 2,
    }
    validate_mapping_document(
        final_mapping, target_count=target_count, repo_root=repo_root
    )
    coverage = build_crosswalk_coverage_report(
        final_mapping, target_count=target_count, repo_root=repo_root
    )

    fallback_rows = [
        row for row in final_mapping["rows"] if row["source"]["kind"] == "english_fallback"
    ]
    historical_fallback_rows = [
        row
        for row in interim_mapping["rows"]
        if row["source"]["kind"] == "english_fallback"
    ]
    fallback_ids = {row["target_id"] for row in historical_fallback_rows}
    fallback_payload_groups: Dict[str, List[str]] = defaultdict(list)
    for target_id in sorted(fallback_ids):
        payload_hash = _sha256_bytes(
            english_entries[int(target_id, 16)].encoded_bytes
        )
        fallback_payload_groups[payload_hash].append(target_id)

    queue_targets = []
    for row in historical_fallback_rows:
        target_id = row["target_id"]
        structural = residual_by_id.get(target_id)
        sites: List[Mapping[str, Any]] = []
        if structural is not None:
            sites.extend(structural.get("target_sites", []))
        if target_id in collision_by_id:
            sites.extend(collision_by_id[target_id].get("target_sites", []))
        if target_id in proposal_by_id:
            sites.extend(
                proposal_by_id[target_id].get("evidence", {}).get(
                    "target_sites", []
                )
            )
        sites_out = _deduplicate_sites(sites)
        subsystem = row["verification"]["subsystem"]
        if subsystem == "unclassified":
            subsystem = _family_for_sites(int(target_id, 16), sites_out)
        reason_class, reason_text = _queue_reason(
            target_id=target_id,
            original=original_by_id[target_id],
            structural_residual=structural,
            donor_payload_variants=donor_variants.get(target_id, 0),
        )
        english = _canonical_english(
            english_entries[int(target_id, 16)], definitions
        )
        payload_hash = english["english_payload_sha256"]
        queue_targets.append(
            {
                **english,
                "grouping": {
                    "english_payload_group": payload_hash,
                    "group_target_ids": fallback_payload_groups[payload_hash],
                    "reason_class": reason_class,
                    "subsystem": subsystem,
                },
                "reason_no_source_mapping": reason_text,
                "reference_sites": sites_out,
                "subsystem": subsystem,
                "suggested_key": _suggested_key(
                    subsystem, english_entries[int(target_id, 16)]
                ),
                "target_id": target_id,
            }
        )

    queue_reason_counts = Counter(
        row["grouping"]["reason_class"] for row in queue_targets
    )
    queue_subsystem_counts = Counter(row["subsystem"] for row in queue_targets)
    rebuilt_queue = {
        "authoritative_target_map_sha256": current_interim_map_sha256,
        "kind": AUTHORED_QUEUE_KIND,
        "note": (
            "Intermediate authored-translation queue. It contains exactly every "
            "remaining English fallback and does not itself provide translations."
        ),
        "schema_version": AUTHORED_QUEUE_SCHEMA_VERSION,
        "summary": {
            "reason_counts": dict(sorted(queue_reason_counts.items())),
            "subsystem_counts": dict(sorted(queue_subsystem_counts.items())),
            "target_count": len(queue_targets),
        },
        "targets": queue_targets,
    }
    if {row["target_id"] for row in queue_targets} != fallback_ids:
        raise FinalMappingError(
            "authored queue does not equal historical pre-authored fallback targets"
        )
    historical_rebuild = deepcopy(rebuilt_queue)
    historical_rebuild["authoritative_target_map_sha256"] = source_map_sha256
    if canonical_json_bytes(historical_rebuild) != canonical_json_bytes(
        authored_queue_data
    ):
        raise FinalMappingError(
            "historical authored queue differs from deterministic rebuild: "
            f"rebuilt={_sha256_bytes(canonical_json_bytes(historical_rebuild))} "
            f"committed={_sha256_bytes(canonical_json_bytes(authored_queue_data))}"
        )
    queue = deepcopy(authored_queue_data)

    precedence_counts = Counter(promoted.values())
    report = {
        "authoritative": False,
        "inputs": {
            "febuilder_alignment_evidence_sha256": _json_hash(febuilder_data),
            "original_target_map_sha256": _json_hash(
                {"rows": original_rows, "target_count": target_count}
            ),
            "runtime_authored_catalog_sha256": {
                locale: _json_hash(runtime_authored_catalogs[locale])
                for locale in ("ja", "zh-Hans")
            },
            "source_authored_queue_sha256": _sha256_bytes(
                canonical_json_bytes(authored_queue_data)
            ),
            "historical_pre_authored_target_map_sha256": source_map_sha256,
            "current_pre_authored_target_map_sha256": current_interim_map_sha256,
            "structural_completion_evidence_sha256": _json_hash(structural_data),
        },
        "kind": FINAL_REPORT_KIND,
        "note": (
            "Evidence report for the deterministic final-mapping promotion pass. "
            "The source queue is retained byte-identically as fulfilled history."
        ),
        "policy": {
            "final_delivery_requires_zero_fallback": True,
            "historical_fulfilled_queue_retained": True,
            "historical_queue_map_hash_is_immutable_provenance": True,
            "historical_queue_map_hash_matches_current": (
                source_map_sha256 == current_interim_map_sha256
            ),
            "numeric_or_proximity_promotion_permitted": False,
        },
        "promotion_counts": dict(sorted(precedence_counts.items())),
        "schema_version": FINAL_REPORT_SCHEMA_VERSION,
        "summary": {
            "authored_queue_target_count": len(queue_targets),
            "fallback_target_count": len(fallback_rows),
            "fulfilled_authored_queue_target_count": len(queue_targets),
            "promoted_target_count": len(promoted),
            "target_count": target_count,
            "translated_target_count": target_count - len(fallback_rows),
            "unfulfilled_authored_queue_target_count": len(fallback_rows),
        },
    }
    return {
        "coverage": coverage,
        "mapping": final_mapping,
        "queue": queue,
        "report": report,
    }


def require_no_fallback(
    queue: Mapping[str, Any],
    *,
    mapping: Optional[Mapping[str, Any]] = None,
) -> None:
    targets = queue.get("targets")
    if not isinstance(targets, list):
        raise FinalMappingError("authored queue targets are malformed")
    if mapping is None:
        if not targets:
            return
        raise FinalMappingError(
            f"final delivery blocked: {len(targets)} fallback targets remain"
        )
    rows = mapping.get("rows")
    if not isinstance(rows, list):
        raise FinalMappingError("final mapping rows are malformed")
    row_by_id = {
        row.get("target_id"): row
        for row in rows
        if isinstance(row, dict) and isinstance(row.get("target_id"), str)
    }
    fallback_rows = [
        row for row in rows if row.get("source", {}).get("kind") == "english_fallback"
    ]
    unfulfilled = []
    for target in targets:
        if not isinstance(target, dict):
            raise FinalMappingError("authored queue target is malformed")
        row = row_by_id.get(target.get("target_id"))
        source = row.get("source", {}) if isinstance(row, dict) else {}
        if (
            source.get("kind") != "authored"
            or source.get("translation_key") != target.get("suggested_key")
        ):
            unfulfilled.append(target.get("target_id"))
    if fallback_rows or unfulfilled:
        raise FinalMappingError(
            "final delivery blocked: "
            f"{len(fallback_rows)} fallback targets and "
            f"{len(unfulfilled)} unfulfilled authored queue targets remain"
        )


def canonical_artifacts(
    artifacts: Mapping[str, Any],
) -> Dict[str, bytes]:
    return {name: canonical_json_bytes(data) for name, data in artifacts.items()}
