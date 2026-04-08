"""Tests for anvl.config module."""

from anvl.config import path_to_slug


def test_slug_basic_windows_path():
    assert path_to_slug(r"c:\Users\foo\bar") == "c--users-foo-bar"


def test_slug_with_spaces():
    assert path_to_slug(r"c:\Users\foo\My Project") == "c--users-foo-my-project"


def test_slug_onedrive_path():
    slug = path_to_slug(r"c:\Users\jumontes\OneDrive - Grupo Security\Escritorio\Juan Luis\ANVL")
    assert slug == "c--users-jumontes-onedrive---grupo-security-escritorio-juan-luis-anvl"


def test_slug_forward_slashes():
    assert path_to_slug("c:/Users/foo/bar") == "c--users-foo-bar"


def test_slug_no_trailing_dash():
    slug = path_to_slug(r"c:\Users\foo\bar\\")
    assert not slug.endswith("-")
