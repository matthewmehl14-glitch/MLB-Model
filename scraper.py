import os
import requests
import json
import numpy as np
from datetime import datetime

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

def get_pinnacle_odds(api_key):
    """Fetch live Pinnacle ML and Totals from The Odds API"""
    if not api_key: return {}
    url = f"https://api.the-odds-api.com/v4/sports/baseball_mlb/odds/?apiKey={api_key}&bookmakers=pinnacle&markets=h2h,totals&oddsFormat=american"
    try:
        res = requests.get(url)
        res.raise_for_status()
        data = res.json()
        odds_dict = {}
        for game in data:
            home = game.get('home_team')
            away = game.get('away_team')
            if home in TEAM_MAPPING and away in TEAM_MAPPING:
                home_abbr = TEAM_MAPPING[home]
                away_abbr = TEAM_MAPPING[away]
                matchup_key = f"{away_abbr}@{home_abbr}"
                
                if matchup_key in odds_dict:
                    continue
                
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

def american_to_decimal(am_odds):
    if am_odds > 0: return (am_odds / 100.0) + 1.0
    else: return (100.0 / abs(am_odds)) + 1.0

def calculate_ev(prob_pct, am_odds, push_pct=0.0):
    if not am_odds or prob_pct == 0: return None
    prob = prob_pct / 100.0
    p_push = push_pct / 100.0
    dec = american_to_decimal(am_odds)
    ev = (prob * dec) - 1.0 + p_push
    return round(ev * 100, 1)

def get_pitcher_stats(pitcher_id, season):
    if not pitcher_id:
        return {"name": "TBD", "era": 4.50, "ra9": 4.50, "k9": 8.0}
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
        if len(ip_parts) > 1: ip += float(ip_parts[1]) / 3.0
        ra9 = (runs / ip * 9) if ip > 0 else era
        return {"name": name, "era": era, "ra9": round(ra9, 2), "k9": k9}
    return {"name": name, "era": 4.50, "ra9": 4.50, "k9": 8.0}

def agg_lineup_stats(lineup_array, player_hit_stats):
    """Aggregates individual player stats to build a team profile for the Spider Chart and Sim"""
    agg = {"G": 0, "R": 0, "H": 0, "2B": 0, "3B": 0, "HR": 0, "RBI": 0, "BB": 0, "SO": 0, "SB": 0, "AB": 0, "PA": 0, "TB": 0}
    for p in lineup_array:
        pid = p.get('id')
        s = player_hit_stats.get(pid, {})
        agg["G"] += s.get('gamesPlayed', 0)
        agg["R"] += s.get('runs', 0)
        agg["H"] += s.get('hits', 0)
        agg["2B"] += s.get('doubles', 0)
        agg["3B"] += s.get('triples', 0)
        agg["HR"] += s.get('homeRuns', 0)
        agg["RBI"] += s.get('rbi', 0)
        agg["BB"] += s.get('baseOnBalls', 0)
        agg["SO"] += s.get('strikeOuts', 0)
        agg["SB"] += s.get('stolenBases', 0)
        agg["AB"] += s.get('atBats', 0)
        agg["PA"] += s.get('plateAppearances', 0)
        agg["TB"] += s.get('totalBases', 0)
    
    avg = agg["H"] / agg["AB"] if agg["AB"] > 0 else 0
    obp = (agg["H"] + agg["BB"]) / agg["PA"] if agg["PA"] > 0 else 0
    slg = agg["TB"] / agg["AB"] if agg["AB"] > 0 else 0
    ops = obp + slg
    
    return {
        "R": agg["R"], "H": agg["H"], "2B": agg["2B"], "3B": agg["3B"],
        "HR": agg["HR"], "RBI": agg["RBI"], "BB": agg["BB"], "SB": agg["SB"],
        "AVG": round(avg, 3), "OBP": round(obp, 3), "SLG": round(slg, 3), "OPS": round(ops, 3),
        "RS_per_game": (agg["R"] / agg["G"]) * 9 if agg["G"] > 0 else 4.5
    }

def simulate_game(t1_rs, t1_ra, t2_rs, t2_ra, lg_rpg, total_line=None, iterations=10000):
    t1_exp = (t1_rs * t2_ra) / lg_rpg if lg_rpg > 0 else (t1_rs + t2_ra) / 2
    t2_exp = (t2_rs * t1_ra) / lg_rpg if lg_rpg > 0 else (t2_rs + t1_ra) / 2
    t1_sims = np.random.poisson(t1_exp, iterations)
    t2_sims = np.random.poisson(t2_exp, iterations)
    
    decisive = t1_sims != t2_sims
    t1_decisive = t1_sims[decisive]
    t2_decisive = t2_sims[decisive]
    
    if len(t1_decisive) == 0:
        return {"t1_win_prob": 50.0, "t2_win_prob": 50.0, "t1_proj_runs": 0, "t2_proj_runs": 0, "total_proj_runs": 0, "ou_probs": {"over_prob": 0, "under_prob": 0, "push_prob": 0}}
        
    t1_win_pct = (np.sum(t1_decisive > t2_decisive) / len(t1_decisive)) * 100
    t2_win_pct = 100.0 - t1_win_pct
    total_sims = t1_sims + t2_sims
    total_proj_runs = round(float(np.mean(total_sims)), 1)
    
    ou_probs = {"over_prob": 0, "under_prob": 0, "push_prob": 0}
    if total_line:
        over = np.sum(total_sims > total_line)
        under = np.sum(total_sims < total_line)
        push = np.sum(total_sims == total_line)
        ou_probs["over_prob"] = float(over / iterations)
        ou_probs["under_prob"] = float(under / iterations)
        ou_probs["push_prob"] = float(push / iterations)
    
    return {
        "t1_win_prob": round(float(t1_win_pct), 1),
        "t2_win_prob": round(float(t2_win_pct), 1),
        "t1_proj_runs": round(float(np.mean(t1_sims)), 2),
        "t2_proj_runs": round(float(np.mean(t2_sims)), 2),
        "total_proj_runs": total_proj_runs,
        "ou_probs": ou_probs
    }

def generate_mlb_json():
    season = 2026
    today_str = datetime.now().strftime('%Y-%m-%d')
    print(f"Fetching {season} player hitting stats...")
    
    api_key = os.environ.get("ODDS_API_KEY")
    pinnacle_data = get_pinnacle_odds(api_key)
    
    player_stats_url = f"https://statsapi.mlb.com/api/v1/stats?stats=season&group=hitting&playerPool=all&season={season}&sportIds=1"
    player_res = requests.get(player_stats_url).json()
    player_hit_stats = {}
    if 'stats' in player_res and len(player_res['stats']) > 0:
        for split in player_res['stats'][0].get('splits', []):
            pid = split.get('player', {}).get('id')
            if pid:
                player_hit_stats[pid] = split.get('stat', {})
    
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
                    "name": name, "abbr": abbr,
                    "G": g, "R": r, "H": int(s.get('hits', 0)),
                    "2B": int(s.get('doubles', 0)), "3B": int(s.get('triples', 0)),
                    "HR": int(s.get('homeRuns', 0)), "RBI": int(s.get('rbi', 0)),
                    "BB": int(s.get('baseOnBalls', 0)), "SO": int(s.get('strikeOuts', 0)),
                    "SB": int(s.get('stolenBases', 0)), "AVG": float(s.get('avg', '.000')),
                    "OBP": float(s.get('obp', '.000')), "SLG": float(s.get('slg', '.000')),
                    "OPS": float(s.get('ops', '.000'))
                }
                
    league_rpg = total_runs_scored / total_games_played if total_games_played > 0 else 4.5
    
    # Notice the hydration now includes 'lineups'
    schedule_url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={today_str}&hydrate=probablePitcher,lineups"
    schedule_data = requests.get(schedule_url).json()
    todays_games = []
    
    print("Isolating pitching matchups and processing daily starting 9 lineups...")
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
                
                matchup_key = f"{away_abbr}@{home_abbr}"
                pinny = pinnacle_data.get(matchup_key, {})
                total_line = pinny.get('totals', {}).get('point')
                
                away_lineup = game.get('lineups', {}).get('awayPlayers', [])
                home_lineup = game.get('lineups', {}).get('homePlayers', [])
                if not away_lineup: away_lineup = game.get('teams', {}).get('away', {}).get('lineup', [])
                if not home_lineup: home_lineup = game.get('teams', {}).get('home', {}).get('lineup', [])
                    
                lineups_confirmed = len(away_lineup) >= 9 and len(home_lineup) >= 9
                
                if lineups_confirmed:
                    away_offense = agg_lineup_stats(away_lineup, player_hit_stats)
                    home_offense = agg_lineup_stats(home_lineup, player_hit_stats)
                    sim_res = simulate_game(
                        away_offense["RS_per_game"], away_pitcher["ra9"],
                        home_offense["RS_per_game"], home_pitcher["ra9"],
                        league_rpg, total_line
                    )
                else:
                    away_offense = None
                    home_offense = None
                    sim_res = None
                
                market_data = None
                if pinny:
                    away_ml = pinny.get('h2h', {}).get('away')
                    home_ml = pinny.get('h2h', {}).get('home')
                    over_odds = pinny.get('totals', {}).get('over')
                    under_odds = pinny.get('totals', {}).get('under')
                    
                    market_data = {
                        "away_ml": away_ml, "home_ml": home_ml,
                        "total_line": total_line, "over_odds": over_odds, "under_odds": under_odds,
                    }
                    if sim_res:
                        market_data["away_ev"] = calculate_ev(sim_res["t1_win_prob"], away_ml)
                        market_data["home_ev"] = calculate_ev(sim_res["t2_win_prob"], home_ml)
                        market_data["over_ev"] = calculate_ev(sim_res["ou_probs"]["over_prob"] * 100, over_odds, sim_res["ou_probs"]["push_prob"] * 100) if over_odds and total_line else None
                        market_data["under_ev"] = calculate_ev(sim_res["ou_probs"]["under_prob"] * 100, under_odds, sim_res["ou_probs"]["push_prob"] * 100) if under_odds and total_line else None
                    else:
                        market_data["away_ev"] = None
                        market_data["home_ev"] = None
                        market_data["over_ev"] = None
                        market_data["under_ev"] = None
                
                todays_games.append({
                    "away_team": away_abbr,
                    "home_team": home_abbr,
                    "away_pitcher": away_pitcher,
                    "home_pitcher": home_pitcher,
                    "lineup_confirmed": lineups_confirmed,
                    "away_offense": away_offense,
                    "home_offense": home_offense,
                    "simulation": sim_res,
                    "market_data": market_data
                })

    output_data = {
        "date": today_str,
        "last_updated": datetime.utcnow().isoformat() + "Z",
        "teams": teams,
        "todays_games": todays_games
    }
    
    with open('data.json', 'w') as f:
        json.dump(output_data, f, indent=4)
        
    print(f"Success! Model updated for {len(todays_games)} games on the slate.")

if __name__ == "__main__":
    generate_mlb_json()
