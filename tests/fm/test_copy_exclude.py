"""Copy/move exclusion masks: the pure matcher, the engine, and the dialog."""

from pathlib import Path

from dunders.core.vfs import VfsPath
from dunders.fm import actions
from dunders.fm.exclude import (
    DEFAULT_EXCLUDE,
    is_excluded,
    parse_exclude_masks,
    split_masks,
)
from dunders.fm.vfs_engine import transfer
from dunders.fm.vfs_local import default_registry


# --- pure matcher ---------------------------------------------------------

def test_parse_exclude_masks_accepts_every_separator():
    assert parse_exclude_masks("*.pyc, *.log ; node_modules") == (
        "*.pyc", "*.log", "node_modules")
    assert parse_exclude_masks("*.pyc;*.log") == ("*.pyc", "*.log")
    assert parse_exclude_masks("   ") == ()
    assert parse_exclude_masks("") == ()


def test_parse_exclude_masks_keeps_the_negation_prefix():
    # The "-" survives parsing; is_excluded is the only interpreter.
    assert parse_exclude_masks(DEFAULT_EXCLUDE) == (".*", "-.gitignore", "venv*")


def test_split_masks_separates_exclusions_from_exceptions():
    assert split_masks((".*", "-.gitignore", "*.pyc")) == (
        (".*", "*.pyc"), (".gitignore",))
    # A bare "-" carries no pattern and is dropped.
    assert split_masks(("-",)) == ((), ())


def test_default_mask_drops_dotfiles_but_keeps_gitignore():
    masks = parse_exclude_masks(DEFAULT_EXCLUDE)
    assert is_excluded(".git", masks)
    assert is_excluded(".env", masks)
    assert not is_excluded(".gitignore", masks)
    assert not is_excluded("main.py", masks)
    # Virtualenvs too, the trailing ";" adding no empty pattern.
    assert is_excluded("venv", masks)
    assert is_excluded("venv312", masks)
    assert not is_excluded("myvenv", masks)


def test_exception_wins_regardless_of_order():
    for text in ("-.gitignore;.*", ".*;-.gitignore"):
        masks = parse_exclude_masks(text)
        assert not is_excluded(".gitignore", masks)
        assert is_excluded(".git", masks)


def test_exception_without_a_matching_exclusion_changes_nothing():
    # "-" only ever re-includes; it never excludes on its own.
    masks = parse_exclude_masks("-*.py")
    assert not is_excluded("main.py", masks)
    assert not is_excluded("other.txt", masks)


def test_is_excluded_matches_basenames_case_sensitively():
    masks = ("*.pyc", "node_modules")
    assert is_excluded("a.pyc", masks)
    assert is_excluded("node_modules", masks)
    assert not is_excluded("a.py", masks)
    # Case-sensitive on purpose: an over-eager mask silently drops files.
    assert not is_excluded("A.PYC", masks)
    # An empty mask tuple is the "feature off" state.
    assert not is_excluded("anything", ())


# --- local copy (actions fast path) ---------------------------------------

def _tree(root: Path) -> None:
    (root / "src").mkdir(parents=True)
    (root / "src" / "keep.py").write_text("keep")
    (root / "src" / "drop.pyc").write_text("drop")
    (root / "src" / "node_modules").mkdir()
    (root / "src" / "node_modules" / "big.js").write_text("x" * 100)
    (root / "src" / "sub").mkdir()
    (root / "src" / "sub" / "also.pyc").write_text("drop")
    (root / "src" / "sub" / "ok.txt").write_text("ok")


def test_copy_paths_skips_excluded_files_and_prunes_directories(tmp_path):
    _tree(tmp_path)
    dest = tmp_path / "out"
    dest.mkdir()

    res = actions.copy_paths(
        [tmp_path / "src"], dest, exclude=("*.pyc", "node_modules"))

    assert not res.errors
    assert (dest / "src" / "keep.py").exists()
    assert (dest / "src" / "sub" / "ok.txt").exists()
    assert not (dest / "src" / "drop.pyc").exists()
    assert not (dest / "src" / "sub" / "also.pyc").exists()
    # An excluded directory is pruned whole — not even created at the target.
    assert not (dest / "src" / "node_modules").exists()


def test_copy_paths_honours_a_negated_mask(tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / ".git").mkdir()
    (tmp_path / "src" / ".git" / "HEAD").write_text("ref")
    (tmp_path / "src" / ".gitignore").write_text("*.pyc")
    (tmp_path / "src" / "main.py").write_text("x")
    dest = tmp_path / "out"
    dest.mkdir()

    actions.copy_paths(
        [tmp_path / "src"], dest, exclude=parse_exclude_masks(DEFAULT_EXCLUDE))

    assert (dest / "src" / "main.py").exists()
    assert (dest / "src" / ".gitignore").exists()   # kept by "-.gitignore"
    assert not (dest / "src" / ".git").exists()     # pruned by ".*"


def test_copy_paths_progress_total_counts_only_copied_files(tmp_path):
    _tree(tmp_path)
    dest = tmp_path / "out"
    dest.mkdir()
    seen: list[tuple[int, int]] = []

    actions.copy_paths(
        [tmp_path / "src"], dest,
        on_progress=lambda i, n: seen.append((i, n)),
        exclude=("*.pyc", "node_modules"),
    )

    # keep.py + sub/ok.txt == 2 files; the bar must not count what was skipped.
    assert seen, "no progress reported"
    assert seen[-1] == (2, 2)


def test_copy_paths_excluded_top_level_source_is_not_copied(tmp_path):
    (tmp_path / "a.pyc").write_text("x")
    (tmp_path / "a.py").write_text("x")
    dest = tmp_path / "out"
    dest.mkdir()

    res = actions.copy_paths(
        [tmp_path / "a.pyc", tmp_path / "a.py"], dest, exclude=("*.pyc",))

    assert (dest / "a.py").exists()
    assert not (dest / "a.pyc").exists()
    assert res.succeeded == [dest / "a.py"]


# --- through the VFS transfer engine --------------------------------------

def test_transfer_copy_honours_exclude(tmp_path):
    _tree(tmp_path)
    dest = tmp_path / "out"
    dest.mkdir()

    res = transfer(
        default_registry(),
        [VfsPath.local(tmp_path / "src")],
        VfsPath.local(dest),
        mode="copy",
        exclude=("*.pyc", "node_modules"),
    )

    assert not res.errors
    assert (dest / "src" / "keep.py").exists()
    assert not (dest / "src" / "drop.pyc").exists()
    assert not (dest / "src" / "node_modules").exists()


def test_transfer_move_leaves_excluded_files_behind(tmp_path):
    """A masked move must not delete what it deliberately did not copy."""
    _tree(tmp_path)
    dest = tmp_path / "out"
    dest.mkdir()

    res = transfer(
        default_registry(),
        [VfsPath.local(tmp_path / "src")],
        VfsPath.local(dest),
        mode="move",
        exclude=("*.pyc",),
    )

    assert not res.errors
    # Copied across and removed from the source.
    assert (dest / "src" / "keep.py").exists()
    assert not (tmp_path / "src" / "keep.py").exists()
    # Excluded: never copied, and still where it was.
    assert not (dest / "src" / "drop.pyc").exists()
    assert (tmp_path / "src" / "drop.pyc").exists()
    assert (tmp_path / "src" / "sub" / "also.pyc").exists()
    # The directory holding a survivor stays; a fully drained one goes.
    assert (tmp_path / "src" / "sub").exists()
    assert not (tmp_path / "src" / "node_modules").exists()


def test_transfer_without_exclude_move_still_removes_the_source(tmp_path):
    _tree(tmp_path)
    dest = tmp_path / "out"
    dest.mkdir()

    transfer(
        default_registry(),
        [VfsPath.local(tmp_path / "src")],
        VfsPath.local(dest),
        mode="move",
    )

    assert (dest / "src" / "keep.py").exists()
    assert not (tmp_path / "src").exists()
