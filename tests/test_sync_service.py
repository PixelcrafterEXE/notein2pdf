"""Tests for the pure planning logic of the Notein -> Nextcloud sync service.

No network, no rclone, no filesystem beyond what pytest's tmp_path/conftest fixtures give us:
`map_note_path` and `plan_cycle` take plain dicts/lists in and return plain data out.
"""
import pytest

from notein2pdf.sync_service import Action, map_note_path, plan_cycle

MAPPINGS = [{"notein_path": "Mec@TUHH/Archive", "nextcloud_path": "Documents/Mec@TUHH/Archive"}]


# ---------------------------------------------------------------- map_note_path
def test_maps_a_note_directly_under_the_mapped_folder():
    assert map_note_path(["Mec@TUHH", "Archive"], "Formelsammlung", MAPPINGS) == \
        "Documents/Mec@TUHH/Archive/Formelsammlung.pdf"


def test_maps_a_note_in_a_subfolder_of_the_mapped_folder():
    assert map_note_path(["Mec@TUHH", "Archive", "ET2", "GÜ"], "Blatt1", MAPPINGS) == \
        "Documents/Mec@TUHH/Archive/ET2/GÜ/Blatt1.pdf"


def test_matching_is_whitespace_and_case_insensitive_on_notein_side():
    # the real Notein folder is literally "Archive " (trailing space) - must still match
    assert map_note_path(["Mec@TUHH", "Archive "], "x", MAPPINGS) == "Documents/Mec@TUHH/Archive/x.pdf"
    assert map_note_path(["mec@tuhh", "ARCHIVE"], "x", MAPPINGS) == "Documents/Mec@TUHH/Archive/x.pdf"


def test_note_outside_every_mapping_is_out_of_scope():
    assert map_note_path(["Mec@TUHH", "ES"], "Lecture 1", MAPPINGS) is None
    assert map_note_path(["SingING"], "Song", MAPPINGS) is None
    assert map_note_path([], "Root note", MAPPINGS) is None


def test_note_title_and_subfolder_names_are_filesystem_sanitised():
    assert map_note_path(["Mec@TUHH", "Archive"], "A/B: C?", MAPPINGS) == \
        "Documents/Mec@TUHH/Archive/A_B_ C_.pdf"


def test_first_matching_mapping_wins_when_several_could_apply():
    mappings = [{"notein_path": "Mec@TUHH/Archive/ET2", "nextcloud_path": "Special"},
                {"notein_path": "Mec@TUHH/Archive", "nextcloud_path": "General"}]
    assert map_note_path(["Mec@TUHH", "Archive", "ET2"], "x", mappings) == "Special/x.pdf"


# ---------------------------------------------------------------- plan_cycle
def meta(nid, title, parent_id=None, mod=100, trashed=False):
    return {"id": nid, "title": title, "parentId": parent_id, "lastModificationTime": mod, "inTrashBin": trashed}


def folders_tree():
    # Mec@TUHH/Archive/ET2
    return {
        "f-tuhh": {"id": "f-tuhh", "title": "Mec@TUHH", "parentId": None},
        "f-arch": {"id": "f-arch", "title": "Archive", "parentId": "f-tuhh"},
        "f-et2": {"id": "f-et2", "title": "ET2", "parentId": "f-arch"},
        "f-other": {"id": "f-other", "title": "Other", "parentId": None},
    }


def test_new_note_in_scope_is_a_create():
    metas = {"/cache/a": meta("n1", "Blatt1", "f-et2")}
    plan = plan_cycle(metas, folders_tree(), {}, MAPPINGS)
    assert plan == [("n1", Action.CREATE, {"path": "/cache/a", "meta": metas["/cache/a"],
                                           "nc_path": "Documents/Mec@TUHH/Archive/ET2/Blatt1.pdf", "dedup": False})]


def test_new_note_out_of_scope_is_skipped_entirely():
    metas = {"/cache/a": meta("n1", "Lecture 1", "f-other")}
    plan = plan_cycle(metas, folders_tree(), {}, MAPPINGS)
    assert plan == []


def test_unchanged_note_is_a_skip():
    metas = {"/cache/a": meta("n1", "Blatt1", "f-et2", mod=100)}
    state = {"n1": {"mod": 100, "nc_path": "Documents/Mec@TUHH/Archive/ET2/Blatt1.pdf", "title": "Blatt1"}}
    plan = plan_cycle(metas, folders_tree(), state, MAPPINGS)
    assert plan == [("n1", Action.SKIP, {})]


def test_content_change_is_an_update():
    metas = {"/cache/a": meta("n1", "Blatt1", "f-et2", mod=200)}
    state = {"n1": {"mod": 100, "nc_path": "Documents/Mec@TUHH/Archive/ET2/Blatt1.pdf", "title": "Blatt1"}}
    plan = plan_cycle(metas, folders_tree(), state, MAPPINGS)
    assert len(plan) == 1 and plan[0][0] == "n1" and plan[0][1] == Action.UPDATE
    assert plan[0][2]["old_nc_path"] == "Documents/Mec@TUHH/Archive/ET2/Blatt1.pdf"
    assert plan[0][2]["nc_path"] == "Documents/Mec@TUHH/Archive/ET2/Blatt1.pdf"


def test_rename_or_move_with_unchanged_content_is_a_cheap_move_not_a_reconvert():
    metas = {"/cache/a": meta("n1", "Blatt1 renamed", "f-et2", mod=100)}  # title changed, mod did NOT change
    state = {"n1": {"mod": 100, "nc_path": "Documents/Mec@TUHH/Archive/ET2/Blatt1.pdf", "title": "Blatt1"}}
    plan = plan_cycle(metas, folders_tree(), state, MAPPINGS)
    assert plan == [("n1", Action.MOVE, {"old_nc_path": "Documents/Mec@TUHH/Archive/ET2/Blatt1.pdf",
                                         "nc_path": "Documents/Mec@TUHH/Archive/ET2/Blatt1 renamed.pdf"})]


def test_trashing_a_previously_synced_note_deletes_it_on_nextcloud():
    metas = {"/cache/a": meta("n1", "Blatt1", "f-et2", trashed=True)}
    state = {"n1": {"mod": 100, "nc_path": "Documents/Mec@TUHH/Archive/ET2/Blatt1.pdf", "title": "Blatt1"}}
    plan = plan_cycle(metas, folders_tree(), state, MAPPINGS)
    assert plan == [("n1", Action.DELETE, {"nc_path": "Documents/Mec@TUHH/Archive/ET2/Blatt1.pdf", "reason": "trashed"})]


def test_trashing_a_note_never_synced_does_nothing():
    metas = {"/cache/a": meta("n1", "Blatt1", "f-et2", trashed=True)}
    plan = plan_cycle(metas, folders_tree(), {}, MAPPINGS)
    assert plan == []


def test_moving_a_synced_note_out_of_the_mapped_scope_deletes_it_on_nextcloud():
    metas = {"/cache/a": meta("n1", "Blatt1", "f-other")}
    state = {"n1": {"mod": 100, "nc_path": "Documents/Mec@TUHH/Archive/ET2/Blatt1.pdf", "title": "Blatt1"}}
    plan = plan_cycle(metas, folders_tree(), state, MAPPINGS)
    assert plan == [("n1", Action.DELETE, {"nc_path": "Documents/Mec@TUHH/Archive/ET2/Blatt1.pdf", "reason": "out of scope"})]


def test_note_missing_from_this_cycle_entirely_is_flagged_not_deleted():
    # simulates a transient Drive listing gap: don't touch Nextcloud on a guess
    state = {"n1": {"mod": 100, "nc_path": "Documents/Mec@TUHH/Archive/ET2/Blatt1.pdf", "title": "Blatt1"}}
    plan = plan_cycle({}, folders_tree(), state, MAPPINGS)
    assert plan == [("n1", Action.UNKNOWN_VANISHED, {"nc_path": "Documents/Mec@TUHH/Archive/ET2/Blatt1.pdf"})]


def test_multiple_notes_mixed_actions_in_one_cycle():
    metas = {
        "/cache/new": meta("new", "New note", "f-et2", mod=1),
        "/cache/same": meta("same", "Same", "f-et2", mod=1),
        "/cache/skip": meta("skip", "Irrelevant", "f-other", mod=1),
    }
    state = {"same": {"mod": 1, "nc_path": "Documents/Mec@TUHH/Archive/ET2/Same.pdf", "title": "Same"}}
    plan = plan_cycle(metas, folders_tree(), state, MAPPINGS)
    by_id = {nid: action for nid, action, _ in plan}
    assert by_id == {"new": Action.CREATE, "same": Action.SKIP}   # 'skip' note (out of scope) produces no plan entry


def test_same_title_in_one_folder_gets_distinct_names_oldest_keeps_plain():
    a, b = meta("aaaa1111", "Unnamed document", "f-et2"), meta("bbbb2222", "Unnamed document", "f-et2")
    a["creationTime"], b["creationTime"] = 200, 100
    plan = dict((nid, info["nc_path"]) for nid, _, info in
                plan_cycle({"/a": a, "/b": b}, folders_tree(), {}, MAPPINGS))
    assert plan["bbbb2222"] == "Documents/Mec@TUHH/Archive/ET2/Unnamed document.pdf"
    assert plan["aaaa1111"] == "Documents/Mec@TUHH/Archive/ET2/Unnamed document (aaaa).pdf"


def test_drive_properties_override_stale_zip_meta():
    from notein2pdf.sync_service import apply_drive_properties
    n = {"id": "n1", "title": "Old", "parentId": "f-et2", "inTrashBin": False}
    f = {"id": "f1", "title": "Fold", "parentId": None}
    apply_drive_properties({"/a": n}, {"f1": f}, {"n1": {"title": "New", "parentId": "null", "inTrashBin": "true"},
                                                   "f1": {"title": "Fold2", "parentId": "x"}})
    assert n == {"id": "n1", "title": "New", "parentId": None, "inTrashBin": True}
    assert f == {"id": "f1", "title": "Fold2", "parentId": "x"}


def test_unknown_parent_folder_is_skipped_not_put_in_root():
    metas = {"/a": meta("n1", "Blatt", "f-missing")}
    plan = plan_cycle(metas, folders_tree(), {}, [{"notein_path": "", "nextcloud_path": "Documents"}])
    assert [(n, a) for n, a, _ in plan] == [("n1", Action.UNRESOLVED)]


def test_duplicate_files_for_one_note_id_newest_wins():
    old, new = meta("n1", "Unnamed document", "f-et2"), meta("n1", "GÜ1", "f-et2")
    old["lastModificationTime"], new["lastModificationTime"] = 1, 2
    plan = plan_cycle({"/old": old, "/new": new}, folders_tree(), {}, MAPPINGS)
    assert len(plan) == 1 and plan[0][2]["meta"]["title"] == "GÜ1"


def test_unbounded_dot_paper_style_is_rendered_without_warning():
    import json
    import fitz
    from notein2pdf import converter
    theme = json.dumps({"baseTheme": {"color": -15461356},
                        "paperStyle": {"type": "x.UnboundedDotPaperStyle", "requiredItemSpace": 37.5}})
    doc = fitz.open(); page = doc.new_page(width=300, height=300)
    converter.warnings.clear()
    converter.paper_background(page, theme, 0.24, 1920, 1920)
    assert converter.warnings == []
    # dots are lighter than the dark paper: the rendered page must contain more than one colour
    pix = page.get_pixmap(dpi=144)
    assert len({pix.pixel(x, y) for x in range(0, pix.width, 3) for y in range(0, pix.height, 3)}) > 1


def test_save_state_is_atomic_and_roundtrips(tmp_path):
    import json
    from notein2pdf.sync_service import save_state
    p = str(tmp_path / "s.json")
    save_state(p, {"a": {"mod": 1}}); save_state(p, {"a": {"mod": 2}})
    assert json.load(open(p)) == {"a": {"mod": 2}} and not (tmp_path / "s.json.tmp").exists()
