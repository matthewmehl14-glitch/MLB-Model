import os
import requests
import json
import numpy as np
import csv
from datetime import datetime, timezone

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

# 2026 Generalized Park Factors (Runs multiplier)
PARK_FACTORS = {
    "ARI": 0.99, "ATL": 1.01, "BAL": 0.96, "BOS": 1.08, "CHC": 1.02, "CHW": 1.01,
    "CIN": 1.07, "CLE": 0.98, "COL": 1.31, "DET": 0.97, "HOU": 0.98, "KCR": 1.02,
    "LAA": 1.02, "LAD": 1.01, "MIA": 0.96, "MIL": 0.99, "MIN": 1.00, "NYM": 0.95,
    "NYY": 0.98, "OAK": 0.94, "PHI": 1.02, "PIT": 0.97, "SDP": 0.95, "SEA": 0.92,
    "SFG": 0.95, "STL": 0.95, "TBR": 0.96, "TEX": 1.00, "TOR": 0.99, "WSN": 0.99
}

def american_to_decimal(am_odds):
    if am_odds > 0: return (am_odds / 100.0) + 1.0
    else: return (100.0 / abs(am_odds)) + 1.0

def implied_probability(am_odds):
    dec = american_to_decimal(am_odds)
    return 1 / dec

def devig_pinnacle(away_odds, home_odds):
    if not away_odds or not home_odds: return None, None
    away_prob = implied_probability(away_odds)
    home_prob = implied_probability(home_odds)
    total_implied = away_prob + home_prob
    return away_prob / total_implied, home_prob / total_implied

def calculate_ev_and_kelly(model_prob_pct, am_odds, push_pct=0.0, kelly_multiplier=0.25):
    if not am_odds or model_prob_pct == 0: return None, 0.0
    prob = model_prob_pct / 100.0
    p_push = push_pct / 100.0
    dec = american_to_decimal(am_odds)
    
    ev = (prob * dec) - 1.0 + p_push
    b = dec - 1.0
    q = 1.0 - prob - p_push
    kelly_pct = ((b * prob) - q) / b if b > 0 else 0
    kelly_pct = max(0, kelly_pct) * kelly_multiplier
    
    return round(ev * 100, 2), round(kelly_pct * 100, 2)

def get_pinnacle_odds(api_key):
    if not api_key: return {}
    url = f"https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/?apiKey={api_key}&bookmakers=pinnacle&markets=h2h,totals&oddsFormat=american"
    odds_dict = {}
    
    try:
        res = requests.get(url)
        res.raise_for_status()
        data = res.json()
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

        for game in data:
            time_str = game.get('commence_time', '').replace('Z', '+00:00')
            if time_str:
                commence_time = datetime.fromisoformat(time_str).replace(tzinfo=None)
                if commence_time < now_utc:
                    continue 

            home = game.get('home_team')
            away = game.get('away_team')
            if home in TEAM_MAPPING and away in TEAM_MAPPING:
                home_abbr = TEAM_MAPPING[home]
                away_abbr = TEAM_MAPPING[away]
                matchup_key = f"{away_abbr}@{home_abbr}"

                if matchup_key in odds_dict: continue

                game_odds = {'h2h': {}, 'totals': {}}
                for book in game.get('bookmakers', []):
                    if book['key'] == 'pinnacle':
                        for market in book.get('markets', []):
                            if market['key'] == 'h2h':
                                for out in market['outcomes']:
                                    if out['name'] == home: game_odds['h2h']['home'] = out['price']
                                    elif out['name'] == away: game_odds['h2h']['away'] = out['price']
                            elif market['key'] == 'totals':
                                for out in market['outcomes']:
                                    if out['name'] == 'Over':
                                        game_odds['totals']['over'] = out['price']
                                        game_odds['totals']['point'] = out.get('point')
                                    elif out['name'] == 'Under':
                                        game_odds['totals']['under'] = out['price']
                                        
                odds_dict[matchup_key] = game_odds
        return odds_dict
    except Exception as e:
        print(f"Warning: Could not fetch Odds API ({e})")
        return {}

def get_pitcher_stats(pitcher_id, season):
    if not pitcher_id:
        return {"name": "TBD", "era": 4.50, "ra9": 4.50, "k9": 8.0, "ip": 0.0}

    try:
        url = f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}/stats?stats=season&group=pitching&season={season}"
        res = requests.get(url).json()

        person_url = f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}"
        person_res = requests.get(person_url).json()
        name = person_res['people'][0]['fullName'] if 'people' in person_res else "Unknown"

        if 'stats' in res and len(res['stats']) > 0 and len(res['stats'][0]['splits']) > 0:
            stats = res['stats'][0]['splits'][0]['stat']
            era = float(stats.get('era', 4.50))
            k9 = float(stats.get('strikeoutsPer9Inn', 8.0))
            runs = int(stats.get('runs', 0))
            ip_str = str(stats.get('inningsPitched', '0.0'))

            ip_parts = ip_str.split('.')
            ip = float(ip_parts[0])
            if len(ip_parts) > 1:
                ip += float(ip_parts[1]) / 3.0

            ra9 = (runs / ip * 9) if ip > 0 else era
            return {"name": name, "era": era, "ra9": round(ra9, 2), "k9": k9, "ip": round(ip, 1)}

        return {"name": name, "era": 4.50, "ra9": 4.50, "k9": 8.0, "ip": 0.0}
    except Exception:
        return {"name": "Unknown", "era": 4.50, "ra9": 4.50, "k9": 8.0, "ip": 0.0}

def simulate_game_advanced(t1rs, t1ra, t2rs, t2ra, lgrpg, total_line=None, park_factor=1.0, iterations=10000):
    t1_exp = ((t1rs * t2ra) / lgrpg) * park_factor if lgrpg > 0 else ((t1rs + t2ra) / 2) * park_factor
    t2_exp = ((t2rs * t1ra) / lgrpg) * park_factor if lgrpg > 0 else ((t2rs + t1ra) / 2) * park_factor

    variance_multiplier = 1.25 
    
    def generate_neg_binom(mean, size):
        variance = mean * variance_multiplier
        if variance <= mean: return np.random.poisson(mean, size) 
        p = mean / variance
        n = (mean**2) / (variance - mean)
        return np.random.negative_binomial(n, p, size)

    t1_sims = generate_neg_binom(t1_exp, iterations)
    t2_sims = generate_neg_binom(t2_exp, iterations)

    ties = t1_sims == t2_sims
    while np.any(ties):
        tie_count = np.sum(ties)
        t1_sims[ties] += generate_neg_binom(0.5, tie_count)
        t2_sims[ties] += generate_neg_binom(0.5, tie_count)
        ties = t1_sims == t2_sims

    t1_win_pct = (np.sum(t1_sims > t2_sims) / iterations) * 100
    t2_win_pct = (np.sum(t2_sims > t1_sims) / iterations) * 100

    total_sims = t1_sims + t2_sims
    total_proj_runs = float(np.mean(total_sims))

    ou_probs = {"over_prob": 0, "under_prob": 0, "push_prob": 0}
    if total_line:
        ou_probs["over_prob"] = float(np.sum(total_sims > total_line) / iterations)
        ou_probs["under_prob"] = float(np.sum(total_sims < total_line) / iterations)
        ou_probs["push_prob"] = float(np.sum(total_sims == total_line) / iterations)

    return {
        "t1_win_prob": round(t1_win_pct, 2),
        "t2_win_prob": round(t2_win_pct, 2),
        "t1_proj_runs": round(float(np.mean(t1_sims)), 2),
        "t2_proj_runs": round(float(np.mean(t2_sims)), 2),
        "total_proj_runs": round(total_proj_runs, 2),
        "ou_probs": ou_probs
    }

def generate_mlb_json():
    season = 2026
    today_str = datetime.now().strftime('%Y-%m-%d')
    print(f"Fetching {season} MLB stats and parsing schedule for {today_str}...")

    api_key = os.environ.get("ODDS_API_KEY")
    pinnacle_data = get_pinnacle_odds(api_key)

    hit_url = f"https://statsapi.mlb.com/api/v1/teams/stats?season={season}&stats=season&group=hitting&sportIds=1"
    hit_data = requests.get(hit_url).json()

    teams = {}
    total_runs_scored = 0
    total_games_played = 0

    if 'stats' in hit_data and hit_data['stats']:
        for split in hit_data['stats'][0]['splits']:
            name = split['team']['name']
            if name in TEAM_MAPPING:
                abbr = TEAM_MAPPING[name]
                s = split['stat']
                g = int(s.get('gamesPlayed', 1))
                r = int(s.get('runs', 0))

                total_runs_scored += r
                total_games_played += g

                teams[abbr] = {
                    "name": name, "abbr": abbr, "G": g, "R": r,
                    "RS_per_game": r / g if g > 0 else 4.5
                }

    league_rpg = total_runs_scored / total_games_played if total_games_played > 0 else 4.5

    pitch_url = f"https://statsapi.mlb.com/api/v1/teams/stats?season={season}&stats=season&group=pitching&sportIds=1"
    pitch_data = requests.get(pitch_url).json()
    team_pitching_ra9 = {}

    if 'stats' in pitch_data and pitch_data['stats']:
        for split in pitch_data['stats'][0]['splits']:
            name = split['team']['name']
            if name in TEAM_MAPPING:
                abbr = TEAM_MAPPING[name]
                r = int(split['stat'].get('runs', 0))
                ip_str = str(split['stat'].get('inningsPitched', '0.0'))
                ip_parts = ip_str.split('.')
                ip = float(ip_parts[0])
                if len(ip_parts) > 1: ip += float(ip_parts[1]) / 3.0
                team_pitching_ra9[abbr] = (r / ip * 9) if ip > 0 else league_rpg

    schedule_url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={today_str}&hydrate=probablePitcher"
    schedule_data = requests.get(schedule_url).json()

    todays_games = []
    print("Isolating starting pitching matchups and applying regression...")
    
    if 'dates' in schedule_data and len(schedule_data['dates']) > 0:
        for game in schedule_data['dates'][0]['games']:
            away_name = game['teams']['away']['team']['name']
            home_name = game['teams']['home']['team']['name']

            if away_name in TEAM_MAPPING and home_name in TEAM_MAPPING:
                away_abbr = TEAM_MAPPING[away_name]
                home_abbr = TEAM_MAPPING[home_name]

                away_pitcher_id = game['teams']['away'].get('probablePitcher', {}).get('id')
                home_pitcher_id = game['teams']['home'].get('probablePitcher', {}).get('id')

                away_pitcher = get_pitcher_stats(away_pitcher_id, season)
                home_pitcher = get_pitcher_stats(home_pitcher_id, season)

                away_ip, away_raw = away_pitcher["ip"], away_pitcher["ra9"]
                home_ip, home_raw = home_pitcher["ip"], home_pitcher["ra9"]

                away_adj_ra9 = ((away_ip * away_raw) + (70 * league_rpg)) / (away_ip + 70)
                home_adj_ra9 = ((home_ip * home_raw) + (70 * league_rpg)) / (home_ip + 70)

                away_bp_ra9 = team_pitching_ra9.get(away_abbr, league_rpg)
                home_bp_ra9 = team_pitching_ra9.get(home_abbr, league_rpg)

                away_total_ra9 = (0.62 * away_adj_ra9) + (0.38 * away_bp_ra9)
                home_total_ra9 = (0.62 * home_adj_ra9) + (0.38 * home_bp_ra9)

                park_factor = PARK_FACTORS.get(home_abbr, 1.00)
                matchup_key = f"{away_abbr}@{home_abbr}"
                pinny = pinnacle_data.get(matchup_key, {})
                total_line = pinny.get('totals', {}).get('point')

                sim_res = simulate_game_advanced(
                    teams.get(away_abbr, {}).get("RS_per_game", 4.5), away_total_ra9,
                    teams.get(home_abbr, {}).get("RS_per_game", 4.5), home_total_ra9,
                    league_rpg, total_line, park_factor
                )

                market_data = None
                if pinny:
                    away_ml = pinny.get('h2h', {}).get('away')
                    home_ml = pinny.get('h2h', {}).get('home')
                    over_odds = pinny.get('totals', {}).get('over')
                    under_odds = pinny.get('totals', {}).get('under')
                    
                    away_ev, away_k = calculate_ev_and_kelly(sim_res["t1_win_prob"], away_ml)
                    home_ev, home_k = calculate_ev_and_kelly(sim_res["t2_win_prob"], home_ml)
                    
                    over_ev, over_k = calculate_ev_and_kelly(sim_res["ou_probs"]["over_prob"] * 100, over_odds, sim_res["ou_probs"]["push_prob"] * 100) if over_odds and total_line else (None, 0.0)
                    under_ev, under_k = calculate_ev_and_kelly(sim_res["ou_probs"]["under_prob"] * 100, under_odds, sim_res["ou_probs"]["push_prob"] * 100) if under_odds and total_line else (None, 0.0)

                    market_data = {
                        "away_ml": away_ml, "home_ml": home_ml,
                        "total_line": total_line, "over_odds": over_odds, "under_odds": under_odds,
                        "away_ev": away_ev, "away_kelly": away_k,
                        "home_ev": home_ev, "home_kelly": home_k,
                        "over_ev": over_ev, "over_kelly": over_k,
                        "under_ev": under_ev, "under_kelly": under_k
                    }

                todays_games.append({
                    "away_team": away_abbr,
                    "home_team": home_abbr,
                    "away_pitcher": away_pitcher,
                    "home_pitcher": home_pitcher,
                    "simulation": sim_res,
                    "market_data": market_data
                })

    csv_file = 'projections_history.csv'
    headers = [
        'Date', 'Away_Team', 'Home_Team', 'Game_Status',
        'Away_Win_Prob', 'Home_Win_Prob', 'Away_Proj_Runs', 'Home_Proj_Runs', 'Proj_Total',
        'Pinnacle_Away_ML', 'Pinnacle_Home_ML', 'Pinnacle_Total_Line',
        'Away_EV', 'Away_Kelly', 'Home_EV', 'Home_Kelly', 'Over_EV', 'Over_Kelly', 'Under_EV', 'Under_Kelly',
        'Actual_Away_Runs', 'Actual_Home_Runs', 'Actual_Total'
    ]

    existing_data = {}
    if os.path.exists(csv_file):
        with open(csv_file, mode='r', newline='', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            for row in reader:
                key = f"{row.get('Date', '')}_{row.get('Away_Team', '')}_{row.get('Home_Team', '')}"
                existing_data[key] = row

    for game in todays_games:
        key = f"{today_str}_{game['away_team']}_{game['home_team']}"
        sim = game['simulation']
        mkt = game['market_data'] or {}
        prev = existing_data.get(key, {})

        existing_data[key] = {
            'Date': today_str,
            'Away_Team': game['away_team'],
            'Home_Team': game['home_team'],
            'Game_Status': prev.get('Game_Status', 'Scheduled'),
            'Away_Win_Prob': sim['t1_win_prob'] if sim else 'N/A',
            'Home_Win_Prob': sim['t2_win_prob'] if sim else 'N/A',
            'Away_Proj_Runs': sim['t1_proj_runs'] if sim else 'N/A',
            'Home_Proj_Runs': sim['t2_proj_runs'] if sim else 'N/A',
            'Proj_Total': sim['total_proj_runs'] if sim else 'N/A',
            'Pinnacle_Away_ML': mkt.get('away_ml', 'N/A'),
            'Pinnacle_Home_ML': mkt.get('home_ml', 'N/A'),
            'Pinnacle_Total_Line': mkt.get('total_line', 'N/A'),
            'Away_EV': mkt.get('away_ev', 'N/A'),
            'Away_Kelly': mkt.get('away_kelly', 'N/A'),
            'Home_EV': mkt.get('home_ev', 'N/A'),
            'Home_Kelly': mkt.get('home_kelly', 'N/A'),
            'Over_EV': mkt.get('over_ev', 'N/A'),
            'Over_Kelly': mkt.get('over_kelly', 'N/A'),
            'Under_EV': mkt.get('under_ev', 'N/A'),
            'Under_Kelly': mkt.get('under_kelly', 'N/A'),
            'Actual_Away_Runs': prev.get('Actual_Away_Runs', 'N/A'),
            'Actual_Home_Runs': prev.get('Actual_Home_Runs', 'N/A'),
            'Actual_Total': prev.get('Actual_Total', 'N/A')
        }

    dates_to_check = set([row.get('Date') for row in existing_data.values() if row.get('Game_Status') != 'Final' and row.get('Date')])

    for d in dates_to_check:
        score_url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={d}"
        try:
            score_data = requests.get(score_url).json()
            if 'dates' in score_data and len(score_data['dates']) > 0:
                for game in score_data['dates'][0]['games']:
                    status = game['status']['statusCode']
                    if status in ['F', 'O']:
                        away_name = game['teams']['away']['team']['name']
                        home_name = game['teams']['home']['team']['name']
                        if away_name in TEAM_MAPPING and home_name in TEAM_MAPPING:
                            k = f"{d}_{TEAM_MAPPING[away_name]}_{TEAM_MAPPING[home_name]}"
                            if k in existing_data:
                                a_score = game['teams']['away'].get('score', 0)
                                h_score = game['teams']['home'].get('score', 0)
                                existing_data[k]['Actual_Away_Runs'] = a_score
                                existing_data[k]['Actual_Home_Runs'] = h_score
                                existing_data[k]['Actual_Total'] = a_score + h_score
                                existing_data[k]['Game_Status'] = 'Final'
        except Exception as e:
            print(f"Warning: Could not fetch final scores for {d} ({e})")

    # The extrasaction='ignore' flag prevents the script from crashing if CSV headers ever mismatch
    with open(csv_file, mode='w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=headers, extrasaction='ignore')
        writer.writeheader()
        for key in sorted(existing_data.keys()):
            writer.writerow(existing_data[key])

    output_data = {
        "date": today_str,
        "last_updated": datetime.now(timezone.utc).isoformat() + "Z",
        "teams": teams,
        "todays_games": todays_games
    }

    with open('data.json', 'w') as f:
        json.dump(output_data, f, indent=4)

    print(f"Success! Model updated with pitching splits and regression for {len(todays_games)} games on the slate.")

if __name__ == "__main__":
    generate_mlb_json()
