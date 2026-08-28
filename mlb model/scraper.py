import requests
import json
import numpy as np

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

def simulate_game(t1_rs, t1_ra, t2_rs, t2_ra, lg_rpg, iterations=10000):
    """10,000-run Monte Carlo simulation using Poisson run generation"""
    t1_exp = (t1_rs * t2_ra) / lg_rpg if lg_rpg > 0 else (t1_rs + t2_ra) / 2
    t2_exp = (t2_rs * t1_ra) / lg_rpg if lg_rpg > 0 else (t2_rs + t1_ra) / 2
    
    t1_sims = np.random.poisson(t1_exp, iterations)
    t2_sims = np.random.poisson(t2_exp, iterations)
    
    # Exclude extra-inning ties for clear moneyline projection
    decisive = t1_sims != t2_sims
    t1_decisive = t1_sims[decisive]
    t2_decisive = t2_sims[decisive]
    
    t1_win_pct = (np.sum(t1_decisive > t2_decisive) / len(t1_decisive)) * 100
    t2_win_pct = 100.0 - t1_win_pct
    
    return {
        "t1_win_prob": round(float(t1_win_pct), 1),
        "t2_win_prob": round(float(t2_win_pct), 1),
        "t1_proj_runs": round(float(np.mean(t1_sims)), 2),
        "t2_proj_runs": round(float(np.mean(t2_sims)), 2),
        "total_proj_runs": round(float(np.mean(t1_sims + t2_sims)), 1)
    }

def generate_mlb_json():
    season = 2026
    print(f"Fetching {season} MLB stats and running Monte Carlo simulations...")
    
    hit_url = f"https://statsapi.mlb.com/api/v1/teams/stats?season={season}&stats=season&group=hitting&sportIds=1"
    pitch_url = f"https://statsapi.mlb.com/api/v1/teams/stats?season={season}&stats=season&group=pitching&sportIds=1"
    
    hit_data = requests.get(hit_url).json()
    pitch_data = requests.get(pitch_url).json()
    
    teams = {}
    total_runs_scored = 0
    total_games_played = 0
    
    # 1. Parse Offensive Stats
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
                    "name": name,
                    "abbr": abbr,
                    "G": g,
                    "R": r,
                    "H": int(s.get('hits', 0)),
                    "2B": int(s.get('doubles', 0)),
                    "3B": int(s.get('triples', 0)),
                    "HR": int(s.get('homeRuns', 0)),
                    "RBI": int(s.get('rbi', 0)),
                    "BB": int(s.get('baseOnBalls', 0)),
                    "SO": int(s.get('strikeOuts', 0)),
                    "SB": int(s.get('stolenBases', 0)),
                    "AVG": float(s.get('avg', '.000')),
                    "OBP": float(s.get('obp', '.000')),
                    "SLG": float(s.get('slg', '.000')),
                    "OPS": float(s.get('ops', '.000')),
                    "RS_per_game": r / g if g > 0 else 4.5,
                    "RA_per_game": 4.5
                }
                
    # 2. Parse Defensive Pitching Stats
    if 'stats' in pitch_data and pitch_data['stats']:
        for split in pitch_data['stats'][0]['splits']:
            name = split['team']['name']
            if name in TEAM_MAPPING:
                abbr = TEAM_MAPPING[name]
                s = split['stat']
                g = int(s.get('gamesPlayed', 1))
                ra = int(s.get('runs', 0))
                if abbr in teams:
                    teams[abbr]["RA_per_game"] = ra / g if g > 0 else 4.5

    league_rpg = total_runs_scored / total_games_played if total_games_played > 0 else 4.5
    
    # 3. Build Pairwise Simulation Matrix
    matchups = {}
    for t1 in teams:
        matchups[t1] = {}
        for t2 in teams:
            if t1 != t2:
                sim_res = simulate_game(
                    teams[t1]["RS_per_game"], teams[t1]["RA_per_game"],
                    teams[t2]["RS_per_game"], teams[t2]["RA_per_game"],
                    league_rpg
                )
                matchups[t1][t2] = sim_res

    output_data = {
        "teams": teams,
        "matchups": matchups
    }
    
    with open('data.json', 'w') as f:
        json.dump(output_data, f, indent=4)
        
    print("Success! Updated data.json with spider metrics & Monte Carlo simulations.")

if __name__ == "__main__":
    generate_mlb_json()