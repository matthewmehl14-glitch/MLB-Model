"""
MLB projection model v2

Changes from v1:
  Modeling
    - Run dispersion raised to ~2.0 (configurable) and auto-calibrated from
      projections_history.csv residuals once enough final games exist
    - Team offense, starter RA9 and bullpen RA9 are park-neutralized before the
      venue park factor is applied (no more double-counting)
    - Bullpen component uses reliever-only splits (falls back to full staff)
    - Team offense regressed toward league average
    - Model probability blended with Pinnacle no-vig before EV/Kelly
    - Extra innings simulated inning-by-inning with automatic-runner scoring rate
    - Athletics park factor updated for Sacramento (verify the value)
  Logging / data integrity
    - Rows keyed by date + gamePk (doubleheaders no longer collide)
    - Odds matched to games by team AND nearest commence time
    - Rows freeze once a game starts; logged odds are never overwritten with N/A
    - Postponed / cancelled games resolve instead of being re-queried forever
    - Slate date pinned to a fixed timezone (safe on UTC runners)
    - Lineups_Confirmed is populated
  Housekeeping
    - Shared requests.Session with timeouts
    - One API call per pitcher instead of two
    - Per-game seeded RNG so reruns are reproducible
"""

import os
import csv
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

import numpy as np
import requests

# ============================================================
# CONFIG
# ============================================================
SEASON = 2026
SLATE_TZ = ZoneInfo(os.environ.get("SLATE_TZ", "America/Chicago"))
HTTP_TIMEOUT = 15
ITERATIONS = 10000

DEFAULT_DISPERSION = 2.0        # variance / mean of team runs in a game
MIN_DISPERSION_SAMPLE = 300     # team-games needed before trusting CSV calibration
DISPERSION_BOUNDS = (1.3, 3.0)

STARTER_IP_PRIOR = 70           # IP of league-average performance added to starters
BULLPEN_IP_PRIOR = 150          # IP of league-average performance added to bullpens
OFFENSE_GAMES_PRIOR = 20        # games of league-average offense added to each team
STARTER_WEIGHT = 0.62           # share of innings assigned to the starter

MODEL_WEIGHT = 0.35             # final_prob = w * model + (1 - w) * pinnacle_no_vig
KELLY_MULTIPLIER = 0.25
EXTRA_INNING_RUNS = 0.95        # approx mean runs per half-inning with automatic runner
ODDS_MATCH_WINDOW_HRS = 6

CSV_FILE = "projections_history.csv"
JSON_FILE = "data.json"
MLB_API = "https://statsapi.mlb.com/api/v1"
RESOLVED_STATUSES = {"Final", "Postponed", "Cancelled"}

TEAM_MAPPING = {
    "Arizona Diamondbacks": "ARI", "Atlanta Braves": "ATL", "Baltimore Orioles": "BAL",
    "Boston Red Sox": "BOS", "Chicago Cubs": "CHC", "Chicago White Sox": "CHW",
    "Cincinnati Reds": "CIN", "Cleveland Guardians": "CLE", "Colorado Rockies": "COL",
    "Detroit Tigers": "DET", "Houston Astros": "HOU", "Kansas City Royals": "KCR",
    "Los Angeles Angels": "LAA", "Los Angeles Dodgers": "LAD", "Miami Marlins": "MIA",
    "Milwaukee Brewers": "MIL", "Minnesota Twins": "MIN", "New York Mets": "NYM",
    "New York Yankees": "NYY", "Oakland Athletics": "OAK", "Athletics": "OAK",
    "Philadelphia Phillies": "PHI", "Pittsburgh Pirates": "PIT", "San Diego Padres": "SDP",
    "Seattle Mariners": "SEA", "San Francisco Giants": "SFG", "St. Louis Cardinals": "STL",
    "Tampa Bay Rays": "TBR", "Texas Rangers": "TEX", "Toronto Blue Jays": "TOR",
    "Washington Nationals": "WSN"
}

# Runs multiplier for the home venue.
# "OAK" abbreviation kept for CSV/dashboard continuity; the club plays in Sacramento.
# 1.05 is a placeholder reflecting a hitter-friendly park -- verify before relying on it.
PARK_FACTORS = {
    "ARI": 0.99, "ATL": 1.01, "BAL": 0.96, "BOS": 1.08, "CHC": 1.02, "CHW": 1.01,
    "CIN": 1.07, "CLE": 0.98, "COL": 1.31, "DET": 0.97, "HOU": 0.98, "KCR": 1.02,
    "LAA": 1.02, "LAD": 1.01, "MIA": 0.96, "MIL": 0.99, "MIN": 1.00, "NYM": 0.95,
    "NYY": 0.98, "OAK": 1.05, "PHI": 1.02, "PIT": 0.97, "SDP": 0.95, "SEA": 0.92,
    "SFG": 0.95, "STL": 0.95, "TBR": 0.96, "TEX": 1.00, "TOR": 0.99, "WSN": 0.99
}

CSV_HEADERS = [
    'Date', 'Game_Pk', 'Away_Team', 'Home_Team', 'Game_Status', 'Projection_Time',
    'Away_Pitcher', 'Home_Pitcher',
    'Away_Win_Prob', 'Home_Win_Prob', 'Away_Proj_Runs', 'Home_Proj_Runs', 'Proj_Total',
    'Pinnacle_Away_ML', 'Pinnacle_Home_ML', 'Pinnacle_Total_Line',
    'Pinnacle_Over_Odds', 'Pinnacle_Under_Odds',
    'Away_NoVig_Prob', 'Home_NoVig_Prob', 'Away_Blend_Prob', 'Home_Blend_Prob',
    'Model_Over_Prob', 'Blend_Over_Prob', 'Push_Prob',
    'Away_EV', 'Away_Kelly', 'Home_EV', 'Home_Kelly', 'Over_EV', 'Over_Kelly', 'Under_EV', 'Under_Kelly',
    'Actual_Away_Runs', 'Actual_Home_Runs', 'Actual_Total', 'Lineups_Confirmed'
]

MARKET_FIELDS = [
    'Pinnacle_Away_ML', 'Pinnacle_Home_ML', 'Pinnacle_Total_Line',
    'Pinnacle_Over_Odds', 'Pinnacle_Under_Odds',
    'Away_NoVig_Prob', 'Home_NoVig_Prob', 'Away_Blend_Prob', 'Home_Blend_Prob',
    'Blend_Over_Prob',
    'Away_EV', 'Away_Kelly', 'Home_EV', 'Home_Kelly', 'Over_EV', 'Over_Kelly', 'Under_EV', 'Under_Kelly'
]

SESSION = requests.Session()


# ============================================================
# HELPERS
# ============================================================
def get_json(url):
    res = SESSION.get(url, timeout=HTTP_TIMEOUT)
    res.raise_for_status()
    return res.json()


def parse_ip(ip_str):
    parts = str(ip_str or '0.0').split('.')
    ip = float(parts[0])
    if len(parts) > 1 and parts[1]:
        ip += float(parts[1]) / 3.0
    return ip


def parse_utc(iso_str):
    if not iso_str:
        return None
    return datetime.fromisoformat(iso_str.replace('Z', '+00:00')).astimezone(timezone.utc)


def park_neutral_divisor(abbr):
    # A team plays ~half its games at home, so its raw stats carry ~half its park effect.
    return (1.0 + PARK_FACTORS.get(abbr, 1.0)) / 2.0


def regress(value, sample, prior_sample, prior_value):
    return ((sample * value) + (prior_sample * prior_value)) / (sample + prior_sample)


def is_missing(v):
    return v is None or v == '' or v == 'N/A'


def to_float(v):
    try:
        return None if is_missing(v) else float(v)
    except (TypeError, ValueError):
        return None


# ============================================================
# ODDS
# ============================================================
def american_to_decimal(am_odds):
    if am_odds > 0:
        return (am_odds / 100.0) + 1.0
    return (100.0 / abs(am_odds)) + 1.0


def implied_probability(am_odds):
    return 1.0 / american_to_decimal(am_odds)


def devig_pair(odds_a, odds_b):
    if not odds_a or not odds_b:
        return None, None
    pa, pb = implied_probability(odds_a), implied_probability(odds_b)
    total = pa + pb
    return pa / total, pb / total


def blend(model_p, market_p):
    if market_p is None:
        return model_p
    return MODEL_WEIGHT * model_p + (1.0 - MODEL_WEIGHT) * market_p


def calculate_ev_and_kelly(prob, am_odds, push_prob=0.0, kelly_multiplier=KELLY_MULTIPLIER):
    """prob and push_prob are fractions (0-1). Returns (EV %, Kelly % of bankroll)."""
    if not am_odds or prob is None or prob <= 0:
        return None, 0.0
    dec = american_to_decimal(am_odds)
    ev = (prob * dec) + push_prob - 1.0
    b = dec - 1.0
    q = 1.0 - prob - push_prob
    kelly = ((b * prob) - q) / b if b > 0 else 0.0
    kelly = max(0.0, kelly) * kelly_multiplier
    return round(ev * 100, 2), round(kelly * 100, 2)


def get_pinnacle_odds(api_key):
    """Returns {"AWY@HOM": [ {commence, h2h, totals}, ... ]} -- a list so doubleheaders survive."""
    if not api_key:
        print("Note: ODDS_API_KEY not set; running without market data.")
        return {}
    url = (f"https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/?apiKey={api_key}"
           f"&bookmakers=pinnacle&markets=h2h,totals&oddsFormat=american")
    odds = {}
    try:
        data = get_json(url)
    except Exception as e:
        print(f"Warning: Could not fetch Odds API ({e})")
        return {}

    for game in data:
        home, away = game.get('home_team'), game.get('away_team')
        if home not in TEAM_MAPPING or away not in TEAM_MAPPING:
            continue
        entry = {'commence': parse_utc(game.get('commence_time')), 'h2h': {}, 'totals': {}}
        for book in game.get('bookmakers', []):
            if book.get('key') != 'pinnacle':
                continue
            for market in book.get('markets', []):
                if market['key'] == 'h2h':
                    for out in market['outcomes']:
                        if out['name'] == home:
                            entry['h2h']['home'] = out['price']
                        elif out['name'] == away:
                            entry['h2h']['away'] = out['price']
                elif market['key'] == 'totals':
                    for out in market['outcomes']:
                        if out['name'] == 'Over':
                            entry['totals']['over'] = out['price']
                            entry['totals']['point'] = out.get('point')
                        elif out['name'] == 'Under':
                            entry['totals']['under'] = out['price']
        key = f"{TEAM_MAPPING[away]}@{TEAM_MAPPING[home]}"
        odds.setdefault(key, []).append(entry)
    return odds


def match_odds(pinnacle_data, matchup_key, game_time_utc):
    """Pick the listing for this matchup closest in time to first pitch, within a window."""
    candidates = pinnacle_data.get(matchup_key, [])
    if not candidates or game_time_utc is None:
        return {}
    best, best_gap = None, None
    for c in candidates:
        if c['commence'] is None:
            continue
        gap = abs((c['commence'] - game_time_utc).total_seconds()) / 3600.0
        if gap <= ODDS_MATCH_WINDOW_HRS and (best_gap is None or gap < best_gap):
            best, best_gap = c, gap
    return best or {}


# ============================================================
# STATS
# ============================================================
def get_pitcher_stats(pitcher_id, season):
    default = {"name": "TBD", "era": 4.50, "ra9": 4.50, "k9": 8.0, "ip": 0.0}
    if not pitcher_id:
        return default
    url = (f"{MLB_API}/people/{pitcher_id}"
           f"?hydrate=stats(group=[pitching],type=[season],season={season})")
    try:
        person = get_json(url)['people'][0]
        name = person.get('fullName', 'Unknown')
        for block in person.get('stats', []):
            splits = block.get('splits', [])
            if not splits:
                continue
            s = splits[0]['stat']
            ip = parse_ip(s.get('inningsPitched'))
            era = float(s.get('era', 4.50)) if s.get('era') not in (None, '-.--') else 4.50
            runs = int(s.get('runs', 0))
            ra9 = (runs / ip * 9) if ip > 0 else era
            return {"name": name, "era": era, "ra9": round(ra9, 2),
                    "k9": float(s.get('strikeoutsPer9Inn', 8.0) or 8.0), "ip": round(ip, 1)}
        return {**default, "name": name}
    except Exception as e:
        print(f"Warning: pitcher {pitcher_id} lookup failed ({e})")
        return {**default, "name": "Unknown"}


def get_team_hitting(season):
    data = get_json(f"{MLB_API}/teams/stats?season={season}&stats=season&group=hitting&sportIds=1")
    teams, total_r, total_g = {}, 0, 0
    for split in (data.get('stats') or [{}])[0].get('splits', []):
        name = split['team']['name']
        if name not in TEAM_MAPPING:
            continue
        abbr = TEAM_MAPPING[name]
        s = split['stat']
        g = int(s.get('gamesPlayed', 0))
        r = int(s.get('runs', 0))
        total_r += r
        total_g += g
        teams[abbr] = {
            "name": name, "abbr": abbr, "G": g, "R": r,
            "RS_per_game": r / g if g > 0 else 4.5,
            "H": int(s.get('hits', 0)), "2B": int(s.get('doubles', 0)),
            "3B": int(s.get('triples', 0)), "HR": int(s.get('homeRuns', 0)),
            "RBI": int(s.get('rbi', 0)), "BB": int(s.get('baseOnBalls', 0)),
            "SB": int(s.get('stolenBases', 0)),
            "AVG": float(s.get('avg', 0)), "OBP": float(s.get('obp', 0)),
            "SLG": float(s.get('slg', 0)), "OPS": float(s.get('ops', 0))
        }
    league_rpg = total_r / total_g if total_g > 0 else 4.5
    return teams, league_rpg


def get_team_pitching(season, sit_code=None):
    """Returns {abbr: (ra9, ip)}. With sit_code='rp' requests reliever-only splits."""
    if sit_code:
        url = (f"{MLB_API}/teams/stats?season={season}&stats=statSplits&group=pitching"
               f"&sportIds=1&sitCodes={sit_code}")
    else:
        url = f"{MLB_API}/teams/stats?season={season}&stats=season&group=pitching&sportIds=1"
    data = get_json(url)
    out = {}
    for block in data.get('stats', []):
        for split in block.get('splits', []):
            code = (split.get('split') or {}).get('code')
            if sit_code and code and code != sit_code:
                continue
            name = split.get('team', {}).get('name')
            if name not in TEAM_MAPPING:
                continue
            ip = parse_ip(split['stat'].get('inningsPitched'))
            r = int(split['stat'].get('runs', 0))
            if ip > 0:
                out[TEAM_MAPPING[name]] = (r / ip * 9, ip)
    return out


def get_bullpen_ra9(season):
    try:
        rp = get_team_pitching(season, sit_code='rp')
        if len(rp) >= 25:
            return rp, "reliever splits"
        print("Warning: reliever splits incomplete; falling back to full-staff pitching.")
    except Exception as e:
        print(f"Warning: reliever splits unavailable ({e}); falling back to full-staff pitching.")
    return get_team_pitching(season), "full staff (fallback)"


# ============================================================
# CALIBRATION
# ============================================================
def estimate_dispersion(rows):
    """Variance/mean of actual team runs around projected team runs, from final games."""
    resid, proj = [], []
    for r in rows:
        if r.get('Game_Status') != 'Final':
            continue
        for a_col, p_col in (('Actual_Away_Runs', 'Away_Proj_Runs'), ('Actual_Home_Runs', 'Home_Proj_Runs')):
            a, p = to_float(r.get(a_col)), to_float(r.get(p_col))
            if a is not None and p is not None:
                resid.append(a - p)
                proj.append(p)
    if len(resid) < MIN_DISPERSION_SAMPLE:
        return DEFAULT_DISPERSION, f"default ({len(resid)} team-games logged, need {MIN_DISPERSION_SAMPLE})"
    ratio = float(np.var(resid, ddof=1) / np.mean(proj))
    ratio = min(max(ratio, DISPERSION_BOUNDS[0]), DISPERSION_BOUNDS[1])
    return ratio, f"calibrated from {len(resid)} team-games"


# ============================================================
# SIMULATION
# ============================================================
def simulate_game(away_exp, home_exp, total_line, dispersion, rng, iterations=ITERATIONS):
    def nb(mean, size):
        if mean <= 0:
            return np.zeros(size, dtype=np.int64)
        var = mean * dispersion
        if var <= mean:
            return rng.poisson(mean, size)
        p = mean / var
        n = mean ** 2 / (var - mean)
        return rng.negative_binomial(n, p, size)

    away = nb(away_exp, iterations)
    home = nb(home_exp, iterations)

    # Extra innings: one inning at a time with the automatic-runner scoring rate
    tied = away == home
    while tied.any():
        k = int(tied.sum())
        away[tied] += nb(EXTRA_INNING_RUNS, k)
        home[tied] += nb(EXTRA_INNING_RUNS, k)
        tied = away == home

    total = away + home
    ou = {"over_prob": 0.0, "under_prob": 0.0, "push_prob": 0.0}
    if total_line is not None:
        ou = {
            "over_prob": float(np.mean(total > total_line)),
            "under_prob": float(np.mean(total < total_line)),
            "push_prob": float(np.mean(total == total_line)),
        }

    return {
        "t1_win_prob": round(float(np.mean(away > home)) * 100, 2),
        "t2_win_prob": round(float(np.mean(home > away)) * 100, 2),
        "t1_proj_runs": round(float(np.mean(away)), 2),
        "t2_proj_runs": round(float(np.mean(home)), 2),
        "total_proj_runs": round(float(np.mean(total)), 2),
        "ou_probs": ou
    }


def build_market(sim, pinny):
    if not pinny:
        return None
    away_ml = pinny.get('h2h', {}).get('away')
    home_ml = pinny.get('h2h', {}).get('home')
    total_line = pinny.get('totals', {}).get('point')
    over_odds = pinny.get('totals', {}).get('over')
    under_odds = pinny.get('totals', {}).get('under')

    model_away = sim['t1_win_prob'] / 100.0
    away_nv, home_nv = devig_pair(away_ml, home_ml)
    away_blend = blend(model_away, away_nv)
    home_blend = 1.0 - away_blend

    away_ev, away_k = calculate_ev_and_kelly(away_blend, away_ml)
    home_ev, home_k = calculate_ev_and_kelly(home_blend, home_ml)

    over_ev = under_ev = None
    over_k = under_k = 0.0
    over_blend = None
    ou = sim['ou_probs']
    if total_line is not None and over_odds and under_odds:
        push = ou['push_prob']
        over_nv, _ = devig_pair(over_odds, under_odds)
        market_over = over_nv * (1.0 - push) if over_nv is not None else None
        over_blend = blend(ou['over_prob'], market_over)
        under_blend = max(0.0, 1.0 - push - over_blend)
        over_ev, over_k = calculate_ev_and_kelly(over_blend, over_odds, push)
        under_ev, under_k = calculate_ev_and_kelly(under_blend, under_odds, push)

    r = lambda x: round(x * 100, 2) if x is not None else None
    return {
        "away_ml": away_ml, "home_ml": home_ml,
        "total_line": total_line, "over_odds": over_odds, "under_odds": under_odds,
        "away_novig": r(away_nv), "home_novig": r(home_nv),
        "away_blend": r(away_blend), "home_blend": r(home_blend),
        "over_blend": r(over_blend),
        "away_ev": away_ev, "away_kelly": away_k,
        "home_ev": home_ev, "home_kelly": home_k,
        "over_ev": over_ev, "over_kelly": over_k,
        "under_ev": under_ev, "under_kelly": under_k
    }


# ============================================================
# CSV
# ============================================================
def row_key(date, game_pk=None, away=None, home=None):
    if game_pk not in (None, '', 'N/A'):
        return f"{date}_{game_pk}"
    return f"{date}_{away}_{home}_legacy"


def load_history():
    rows = {}
    if not os.path.exists(CSV_FILE):
        return rows
    with open(CSV_FILE, newline='', encoding='utf-8') as f:
        for row in csv.DictReader(f):
            key = row_key(row.get('Date', ''), row.get('Game_Pk'), row.get('Away_Team'), row.get('Home_Team'))
            rows[key] = row
    return rows


def save_history(rows):
    with open(CSV_FILE, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=CSV_HEADERS, extrasaction='ignore', restval='N/A')
        writer.writeheader()
        for key in sorted(rows):
            writer.writerow(rows[key])


def na(v):
    return 'N/A' if v is None else v


def update_final_scores(rows):
    dates = {r.get('Date') for r in rows.values()
             if r.get('Date') and r.get('Game_Status') not in RESOLVED_STATUSES}
    for d in sorted(dates):
        try:
            data = get_json(f"{MLB_API}/schedule?sportId=1&date={d}")
        except Exception as e:
            print(f"Warning: Could not fetch results for {d} ({e})")
            continue
        for date_block in data.get('dates', []):
            for game in date_block.get('games', []):
                away_name = game['teams']['away']['team']['name']
                home_name = game['teams']['home']['team']['name']
                if away_name not in TEAM_MAPPING or home_name not in TEAM_MAPPING:
                    continue
                key = row_key(d, game.get('gamePk'))
                if key not in rows:
                    key = row_key(d, None, TEAM_MAPPING[away_name], TEAM_MAPPING[home_name])
                if key not in rows or rows[key].get('Game_Status') in RESOLVED_STATUSES:
                    continue

                status = game.get('status', {})
                detailed = status.get('detailedState', '')
                if detailed.startswith('Postponed'):
                    rows[key]['Game_Status'] = 'Postponed'
                elif detailed.startswith('Cancelled'):
                    rows[key]['Game_Status'] = 'Cancelled'
                elif status.get('abstractGameState') == 'Final' and not detailed.startswith('Suspended'):
                    a = game['teams']['away'].get('score')
                    h = game['teams']['home'].get('score')
                    if a is None or h is None:
                        continue
                    rows[key].update({
                        'Actual_Away_Runs': a, 'Actual_Home_Runs': h,
                        'Actual_Total': a + h, 'Game_Status': 'Final'
                    })


# ============================================================
# MAIN
# ============================================================
def generate_mlb_json():
    today_str = datetime.now(SLATE_TZ).date().isoformat()
    now_utc_str = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    print(f"Fetching {SEASON} MLB stats for slate {today_str} ({SLATE_TZ.key})...")

    history = load_history()
    dispersion, dispersion_src = estimate_dispersion(history.values())
    print(f"Run dispersion: {dispersion:.2f} [{dispersion_src}]")

    pinnacle_data = get_pinnacle_odds(os.environ.get("ODDS_API_KEY"))
    teams, league_rpg = get_team_hitting(SEASON)
    bullpen_raw, bullpen_src = get_bullpen_ra9(SEASON)
    print(f"League R/G: {league_rpg:.3f} | Bullpen source: {bullpen_src}")

    # Park-neutral, regressed offense and bullpen per team
    offense, bullpen = {}, {}
    for abbr in set(TEAM_MAPPING.values()):
        t = teams.get(abbr)
        if t:
            neutral = t['RS_per_game'] / park_neutral_divisor(abbr)
            offense[abbr] = regress(neutral, t['G'], OFFENSE_GAMES_PRIOR, league_rpg)
            t['RS_per_game_adj'] = round(offense[abbr], 3)
        else:
            offense[abbr] = league_rpg
        if abbr in bullpen_raw:
            ra9, ip = bullpen_raw[abbr]
            bullpen[abbr] = regress(ra9 / park_neutral_divisor(abbr), ip, BULLPEN_IP_PRIOR, league_rpg)
        else:
            bullpen[abbr] = league_rpg

    schedule = get_json(f"{MLB_API}/schedule?sportId=1&date={today_str}&hydrate=probablePitcher,lineups")
    games = []
    for block in schedule.get('dates', []):
        games.extend(block.get('games', []))

    todays_games = []
    print(f"Simulating {len(games)} scheduled games...")

    for game in games:
        away_name = game['teams']['away']['team']['name']
        home_name = game['teams']['home']['team']['name']
        if away_name not in TEAM_MAPPING or home_name not in TEAM_MAPPING:
            continue
        away, home = TEAM_MAPPING[away_name], TEAM_MAPPING[home_name]
        game_pk = game.get('gamePk')
        game_time = parse_utc(game.get('gameDate'))
        status = game.get('status', {})
        started = status.get('abstractGameState') != 'Preview'
        lineups = game.get('lineups') or {}
        lineups_confirmed = 'Yes' if lineups.get('awayPlayers') and lineups.get('homePlayers') else 'No'

        away_p = get_pitcher_stats(game['teams']['away'].get('probablePitcher', {}).get('id'), SEASON)
        home_p = get_pitcher_stats(game['teams']['home'].get('probablePitcher', {}).get('id'), SEASON)

        away_sp = regress(away_p['ra9'] / park_neutral_divisor(away), away_p['ip'], STARTER_IP_PRIOR, league_rpg)
        home_sp = regress(home_p['ra9'] / park_neutral_divisor(home), home_p['ip'], STARTER_IP_PRIOR, league_rpg)
        away_ra9 = STARTER_WEIGHT * away_sp + (1 - STARTER_WEIGHT) * bullpen[away]
        home_ra9 = STARTER_WEIGHT * home_sp + (1 - STARTER_WEIGHT) * bullpen[home]

        pf = PARK_FACTORS.get(home, 1.0)
        away_exp = (offense[away] * home_ra9 / league_rpg) * pf
        home_exp = (offense[home] * away_ra9 / league_rpg) * pf

        pinny = match_odds(pinnacle_data, f"{away}@{home}", game_time)
        total_line = pinny.get('totals', {}).get('point') if pinny else None

        rng = np.random.default_rng(int(game_pk) if game_pk else None)
        sim = simulate_game(away_exp, home_exp, total_line, dispersion, rng)
        market = build_market(sim, pinny)

        todays_games.append({
            "game_pk": game_pk,
            "game_time": game.get('gameDate'),
            "status": status.get('detailedState'),
            "started": started,
            "lineups_confirmed": lineups_confirmed == 'Yes',
            "away_team": away, "home_team": home,
            "away_pitcher": away_p, "home_pitcher": home_p,
            "simulation": sim,
            "market_data": market
        })

        # ---- CSV logging ----
        key = row_key(today_str, game_pk)
        legacy_key = row_key(today_str, None, away, home)
        prev = history.get(key)
        if prev is None and legacy_key in history:
            prev = history.pop(legacy_key)   # migrate a v1 row for today onto the gamePk key
            prev['Game_Pk'] = game_pk
            history[key] = prev

        if started:
            # Frozen: a projection made after first pitch isn't a valid pre-game record.
            continue

        mkt = market or {}
        ou = sim['ou_probs']
        row = {
            'Date': today_str, 'Game_Pk': game_pk,
            'Away_Team': away, 'Home_Team': home,
            'Game_Status': (prev or {}).get('Game_Status', 'Scheduled'),
            'Projection_Time': now_utc_str,
            'Away_Pitcher': away_p['name'], 'Home_Pitcher': home_p['name'],
            'Away_Win_Prob': sim['t1_win_prob'], 'Home_Win_Prob': sim['t2_win_prob'],
            'Away_Proj_Runs': sim['t1_proj_runs'], 'Home_Proj_Runs': sim['t2_proj_runs'],
            'Proj_Total': sim['total_proj_runs'],
            'Pinnacle_Away_ML': na(mkt.get('away_ml')), 'Pinnacle_Home_ML': na(mkt.get('home_ml')),
            'Pinnacle_Total_Line': na(mkt.get('total_line')),
            'Pinnacle_Over_Odds': na(mkt.get('over_odds')), 'Pinnacle_Under_Odds': na(mkt.get('under_odds')),
            'Away_NoVig_Prob': na(mkt.get('away_novig')), 'Home_NoVig_Prob': na(mkt.get('home_novig')),
            'Away_Blend_Prob': na(mkt.get('away_blend')), 'Home_Blend_Prob': na(mkt.get('home_blend')),
            'Model_Over_Prob': round(ou['over_prob'] * 100, 2) if total_line is not None else 'N/A',
            'Blend_Over_Prob': na(mkt.get('over_blend')),
            'Push_Prob': round(ou['push_prob'] * 100, 2) if total_line is not None else 'N/A',
            'Away_EV': na(mkt.get('away_ev')), 'Away_Kelly': na(mkt.get('away_kelly')),
            'Home_EV': na(mkt.get('home_ev')), 'Home_Kelly': na(mkt.get('home_kelly')),
            'Over_EV': na(mkt.get('over_ev')), 'Over_Kelly': na(mkt.get('over_kelly')),
            'Under_EV': na(mkt.get('under_ev')), 'Under_Kelly': na(mkt.get('under_kelly')),
            'Actual_Away_Runs': (prev or {}).get('Actual_Away_Runs', 'N/A'),
            'Actual_Home_Runs': (prev or {}).get('Actual_Home_Runs', 'N/A'),
            'Actual_Total': (prev or {}).get('Actual_Total', 'N/A'),
            'Lineups_Confirmed': lineups_confirmed
        }

        # Never overwrite previously logged odds with N/A (e.g. line pulled temporarily)
        if prev and market is None and not is_missing(prev.get('Pinnacle_Away_ML')):
            for f in MARKET_FIELDS:
                row[f] = prev.get(f, 'N/A')

        history[key] = row

    update_final_scores(history)
    save_history(history)

    output = {
        "date": today_str,
        "last_updated": now_utc_str,
        "model_config": {
            "dispersion": round(dispersion, 3), "dispersion_source": dispersion_src,
            "model_weight": MODEL_WEIGHT, "kelly_multiplier": KELLY_MULTIPLIER,
            "bullpen_source": bullpen_src, "league_rpg": round(league_rpg, 3)
        },
        "teams": teams,
        "todays_games": todays_games
    }
    with open(JSON_FILE, 'w') as f:
        json.dump(output, f, indent=4)

    print(f"Success! {len(todays_games)} games projected; history saved to {CSV_FILE}.")


if __name__ == "__main__":
    generate_mlb_json()
