from fpl_xpts.scoring import appearance_points, clean_sheet_points, goal_points


def test_basic_fpl_scoring_helpers():
    assert appearance_points(0) == 0
    assert appearance_points(59) == 1
    assert appearance_points(60) == 2
    assert goal_points("MID") == 5
    assert clean_sheet_points("FWD") == 0

