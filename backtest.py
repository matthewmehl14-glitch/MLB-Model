import csv
import os

# --- BACKTEST CONFIGURATION ---
STARTING_BANKROLL = 1000.0
KELLY_MULTIPLIER = 0.25    # Quarter-Kelly staking
ML_EV_HURDLE = 10.0        # 10% EV minimum for ML
TOTAL_EV_HURDLE = 15.0     # 15% EV minimum for Totals (Overs only)

def american_to_decimal(am_odds):
    try:
        odds = float(am_odds)
        if odds > 0: return (odds / 100.0) + 1.0
        else: return (100.0 / abs(odds)) + 1.0
    except ValueError:
        return 0.0

def implied_prob(am_odds):
    dec = american_to_decimal(am_odds)
    return 1.0 / dec if dec > 0 else 0.0

def proportional_devig(away_ml, home_ml):
    """Removes the sportsbook vig to find true probability"""
    away_imp = implied_prob(away_ml)
    home_imp = implied_prob(home_ml)
    vig = away_imp + home_imp
    if vig == 0: return 0.0, 0.0
    return away_imp / vig, home_imp / vig

def calculate_kelly_stake(win_prob_pct, decimal_odds, bankroll):
    """Fractional Kelly Criterion sizing based on EV and bankroll"""
    p = win_prob_pct / 100.0
    b = decimal_odds - 1.0
    if b <= 0: return 0.0
    
    f_star = (p * b - (1.0 - p)) / b
    if f_star > 0:
        return bankroll * (f_star * KELLY_MULTIPLIER)
    return 0.0

def run_backtest():
    bankroll = STARTING_BANKROLL
    wins, losses, pushes = 0, 0, 0
    total_staked = 0.0
    
    if not os.path.exists('projections_history.csv'):
        print("Error: projections_history.csv not found.")
        return
        
    with open('projections_history.csv', 'r', encoding='utf-8') as f:
        reader = csv.DictReader(f)
        for row in reader:
            if row.get('Game_Status') != 'Final':
                continue
                
            try:
                actual_away = float(row['Actual_Away_Runs'])
                actual_home = float(row['Actual_Home_Runs'])
                actual_total = float(row['Actual_Total'])
            except ValueError:
                continue

            # --- MONEYLINE EVALUATION ---
            try:
                away_ev = float(row['Away_EV'])
                home_ev = float(row['Home_EV'])
                away_ml = float(row['Pinnacle_Away_ML'])
                home_ml = float(row['Pinnacle_Home_ML'])
                away_prob = float(row['Away_Win_Prob'])
                home_prob = float(row['Home_Win_Prob'])
                
                # Extract true market probability using proportional devigging
                true_away_prob, true_home_prob = proportional_devig(away_ml, home_ml)
                
                if away_ev >= ML_EV_HURDLE:
                    odds = american_to_decimal(away_ml)
                    stake = calculate_kelly_stake(away_prob, odds, bankroll)
                    if stake > 0:
                        total_staked += stake
                        if actual_away > actual_home:
                            bankroll += stake * (odds - 1.0)
                            wins += 1
                        else:
                            bankroll -= stake
                            losses += 1
                            
                elif home_ev >= ML_EV_HURDLE:
                    odds = american_to_decimal(home_ml)
                    stake = calculate_kelly_stake(home_prob, odds, bankroll)
                    if stake > 0:
                        total_staked += stake
                        if actual_home > actual_away:
                            bankroll += stake * (odds - 1.0)
                            wins += 1
                        else:
                            bankroll -= stake
                            losses += 1
            except (ValueError, TypeError):
                pass
                
            # --- TOTALS EVALUATION (OVERS ONLY) ---
            try:
                over_ev = float(row['Over_EV'])
                total_line = float(row['Pinnacle_Total_Line'])
                
                if over_ev >= TOTAL_EV_HURDLE:
                    # Assuming standard -110 odds for Totals
                    odds = american_to_decimal(-110)
                    f_star = (over_ev / 100.0) / (odds - 1.0)
                    if f_star > 0:
                        stake = bankroll * (f_star * KELLY_MULTIPLIER)
                        total_staked += stake
                        if actual_total > total_line:
                            bankroll += stake * (odds - 1.0)
                            wins += 1
                        elif actual_total < total_line:
                            bankroll -= stake
                            losses += 1
                        else:
                            pushes += 1
            except (ValueError, TypeError):
                pass
                
    roi = ((bankroll - STARTING_BANKROLL) / total_staked) * 100 if total_staked > 0 else 0.0
    
    print(f"\n--- MLB QUARTER-KELLY BACKTEST RESULTS ---")
    print(f"Starting Bankroll: ${STARTING_BANKROLL:.2f}")
    print(f"Ending Bankroll:   ${bankroll:.2f}")
    print(f"Total Staked:      ${total_staked:.2f}")
    print(f"Record:            {wins}-{losses}-{pushes}")
    print(f"Yield (ROI):       {roi:.2f}%\n")

if __name__ == '__main__':
    run_backtest()
