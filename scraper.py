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

def get_pitcher_stats(pitcher_id, season):
    """Fetch 2026 season stats for a specific pitcher"""
    if not pitcher_id:
        # League average fallback if no probable pitcher is announced
        return {"name": "TBD", "era": 4.50, "ra9": 4.50, "k9": 8.0}
        
    url = f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}/stats?stats=season&group=pitching&season={season}"
    res = requests.get(url).json()
    
    # Also get the pitcher's name
    person_url = f"https://statsapi.mlb.com/api/v1/people/{pitcher_id}"
    person_res = requests.get(person_url).json()
    name = person_res['people'][0]['fullName'] if 'people' in person_res else "Unknown"

    if 'stats' in res and len(res['stats']) > 0 and len(res['stats'][0]['splits']) > 0:
        stats = res['stats'][0]['splits'][0]['stat']
        era = float(stats.get('era', 4.50))
        k9 = float(stats.get('strikeoutsPer9Inn', 8.0))
        
        # Calculate true Runs Allowed per 9 (includes unearned runs, better for simulation)
        runs = int(stats.get('runs', 0))
        ip_str = str(stats.get('inningsPitched', '0.0'))
        
        # Handle baseball's thirds of an inning notation (e.g., 50.1 = 50.333)
        ip_parts = ip_str.split('.')
        ip = float(ip_parts[0])
        if len(ip_parts) > 1:
            ip += float(ip_parts[1]) / 3.0
            
        ra9 = (runs / ip * 9) if ip > 0 else era
        
        return {"name": name, "era": era, "ra9": round(ra9, 2), "k9": k9}
        
    return {"name": name, "era": 4.50, "ra9": 4.50, "k9": 8.0}

def simulate_game(t1_rs, t1_ra, t2_rs, t2_ra, lg_rpg, iterations=10000):
    """10,000-run Monte Carlo simulation using Poisson run generation"""
    t1_exp = (t1_rs * t2_ra) / lg_rpg if lg_rpg > 0 else (t1_rs + t2_ra) / 2
    t2_exp = (t2_rs * t1_ra) / lg_rpg if lg_rpg > 0 else (t2_rs + t1_ra) / 2
    
    t1_sims = np.random.poisson(t1_exp, iterations)
    t2_sims = np.random.poisson(t2_exp, iterations)
    
    decisive = t1_sims != t2_sims
    t1_decisive = t1_sims[decisive]
    t2_decisive = t2_sims[decisive]
    
    if len(t1_decisive) == 0:
        return {"t1_win_prob": 50.0, "t2_win_prob": 50.0, "t1_proj_runs": 0, "t2_proj_runs": 0, "total_proj_runs": 0}
        
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
    # Force the script to dynamically grab today's date for the slate
    today_str = datetime.now().strftime('%Y-%m-%d')
    print(f"Fetching 2026 MLB stats and parsing schedule for {today_str}...")
    
    # 1. Fetch Offensive Team Stats
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
                    "name": name,
                    "abbr": abbr,
                    "G": g, "R": r, "H": int(s.get('hits', 0)),
                    "2B": int(s.get('doubles', 0)), "3B": int(s.get('triples', 0)),
                    "HR": int(s.get('homeRuns', 0)), "RBI": int(s.get('rbi', 0)),
                    "BB": int(s.get('baseOnBalls', 0)), "SO": int(s.get('strikeOuts', 0)),
                    "SB": int(s.get('stolenBases', 0)), "AVG": float(s.get('avg', '.000')),
                    "OBP": float(s.get('obp', '.000')), "SLG": float(s.get('slg', '.000')),
                    "OPS": float(s.get('ops', '.000')),
                    "RS_per_game": r / g if g > 0 else 4.5
                }
                
    league_rpg = total_runs_scored / total_games_played if total_games_played > 0 else 4.5
    
    # 2. Fetch Today's Schedule and Probable Pitchers
    schedule_url = f"https://statsapi.mlb.com/api/v1/schedule?sportId=1&date={today_str}&hydrate=probablePitcher"
    schedule_data = requests.get(schedule_url).json()
    
    todays_games = []
    
    print("Isolating starting pitching matchups...")
    if 'dates' in schedule_data and len(schedule_data['dates']) > 0:
        for game in schedule_data['dates'][0]['games']:
            away_name = game['teams']['away']['team']['name']
            home_name = game['teams']['home']['team']['name']
            
            if away_name in TEAM_MAPPING and home_name in TEAM_MAPPING:
                away_abbr = TEAM_MAPPING[away_name]
                home_abbr = TEAM_MAPPING[home_name]
                
                away_pitcher_id = game['teams']['away'].get('probablePitcher', {}).get('id')
                home_pitcher_id = game['teams']['home'].get('probablePitcher', {}).get('id')
                
                # Fetch specific metrics for today's starters
                away_pitcher = get_pitcher_stats(away_pitcher_id, season)
                home_pitcher = get_pitcher_stats(home_pitcher_id, season)
                
                # 3. Run Monte Carlo using specific Starter RA9 instead of Team ERA
                sim_res = simulate_game(
                    teams[away_abbr]["RS_per_game"], away_pitcher["ra9"],
                    teams[home_abbr]["RS_per_game"], home_pitcher["ra9"],
                    league_rpg
                )
                
                todays_games.append({
                    "away_team": away_abbr,
                    "home_team": home_abbr,
                    "away_pitcher": away_pitcher,
                    "home_pitcher": home_pitcher,
                    "simulation": sim_res
                })

    output_data = {
        "date": today_str,
        "teams": teams,
        "todays_games": todays_games
    }
    
    with open('data.json', 'w') as f:
        json.dump(output_data, f, indent=4)
        
    print(f"Success! Model updated with pitching splits for {len(todays_games)} games on the slate.")

if __name__ == "__main__":
    generate_mlb_json()