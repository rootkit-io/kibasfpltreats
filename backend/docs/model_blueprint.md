# Model Blueprint

## Principles

The model should be automatic by default and editable by exception. FPL player ID is the primary key. Any Understat, odds, or historical-row match must resolve back to an FPL `element` ID before it enters the modelling tables.

The app should show both expected value and uncertainty. A 4.8 xPts player with a tight 3-7 range is different from a 4.8 xPts player with a 0-14 range.

## Live FPL Inputs

Use the official Fantasy Premier League endpoints:

- `bootstrap-static/`: players, teams, events, game settings, current expected stats, prices, status, news, ownership, set-piece order fields.
- `fixtures/`: current fixture list, kickoff times, home/away teams, difficulty, results once played.
- `element-summary/{element_id}/`: each player history, upcoming fixtures, previous seasons.
- `event/{event_id}/live/`: actual points and event stats for completed/current gameweeks.

## Historical Inputs

Use historical data for validation, not only for modelling:

- vaastav/Fantasy-Premier-League: historical FPL gameweek files, player season files, fixtures, and data dictionary.
- Understat: player/team xG/xA/xGA history when available.
- Football-Data.co.uk or Odds API: historical and current betting odds for market-implied team goal expectations.

## Feature Tables

Core tables:

- `players_live`: one row per FPL player from `bootstrap-static`.
- `fixtures_live`: one row per fixture from FPL.
- `player_fixture_history`: one row per player-match from `element-summary`.
- `player_gw_actuals`: one row per player-gameweek from `event/{gw}/live`.
- `team_strength`: attack, defence, home advantage, clean-sheet strength, save environment.
- `player_rates`: shrunken attacking, assist, defcon, save, card, start, and minute rates.
- `fixture_forecasts`: expected home/away goals and clean-sheet probabilities.
- `player_fixture_forecasts`: player-level xG, xA, xPts components per fixture.
- `mc_player_week`: risk brackets and percentiles.

## xPts Engine

1. Estimate fixture goal lambdas for each team.
2. Estimate player minutes with start/sub/no-show probabilities.
3. Shrink low-minute player rates toward position/team priors.
4. Allocate team xG and xA to players at fixture level, conserving team totals.
5. Apply FPL scoring rules:
   - appearance;
   - goals by position;
   - assists;
   - clean sheets;
   - goalkeeper saves and penalty saves;
   - defensive contribution thresholds;
   - cards, own goals, penalty misses;
   - goals conceded;
   - expected bonus.

## Monte Carlo Engine

The simulation should be fixture-level, not player-week-only.

For each simulated fixture:

1. Draw shared home and away goals from forecast lambdas.
2. Draw player minutes from a distribution that preserves expected minutes.
3. Allocate team goals to scorers using xG shares.
4. Allocate assists conditionally using xA shares and an assist probability.
5. Apply shared clean-sheet and goals-conceded outcomes.
6. Draw saves, cards, penalties, defcon counts, and own goals.
7. Compute BPS-like scores and award bonus by ranking players within the same simulated match.
8. Aggregate fixture simulations to player-gameweek and squad outputs.

This avoids impossible outcomes like defenders from the same team receiving different goals-conceded results.

## Validation

Every model run should produce an audit:

- missing players or unmatched IDs;
- blank fixtures;
- low-minute rate inflation;
- team xG/xA conservation errors;
- penalty share assigned to zero-minute players;
- Monte Carlo mean drifting too far from deterministic xPts;
- calibration curves for P1 return, clean sheet probability, and bracket probabilities.

