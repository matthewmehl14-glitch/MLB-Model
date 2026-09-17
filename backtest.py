import csv
import os

# --- BACKTEST CONFIGURATION ---
FLAT_STAKE = 25.0

# Edge Hurdles (filters out low-conviction noise)
MIN_ML_EV = 5.0
MAX_ML_EV = 35.0

MIN_TOTAL_EV = 15.0        
MAX_TOTAL_EV = 45.0
PLAY_UNDERS = False        

def american_to_decimal(am_odds):
    """Converts American odds to Decimal for payout calculations."""
    try:
        odds = float(am_odds)
        if odds > 0:
            return (odds / 100.0) + 1.0
        else:
            return (100.0 / abs(odds)) + 1.0
    except (ValueError, TypeError):
        return 0.0

def evaluate_bet(market_type, pick, row, bet_amount):
    """Grades the bet against actual historical outcomes."""
    actual_away = float(row['Actual_Away_Runs'])
    actual_home = float(row['Actual_Home_Runs'])
    actual_total = float(row['Actual_Total'])
    
    if market_type == 'ML':
        odds = float(row['Pinnacle_Away_ML'] if pick == 'away' else row['Pinnacle_Home_ML'])
        if actual_away == actual_home:
            return 0.0, 'push'
        won = (actual_away > actual_home) if pick == 'away' else (actual_home > actual_away)
        profit = bet_amount * (american_to_decimal(odds) - 1.0) if won else -bet_amount
        return profit, ('win' if won else 'loss')

    elif market_type == 'TOTAL':
        line = float(row['Pinnacle_Total_Line'])
        # Pinnacle standard totals assume roughly -110 unless specified
        if actual_total == line:
            return 0.0, 'push'
        won = (actual_total > line) if pick == 'over' else (actual_total < line)
        profit = bet_amount * (100.0 / 110.0) if won else -bet_amount
        return profit, ('win' if won else 'loss')

def run_backtest(target_date='ALL'):
    csv_file = 'projections_history.csv'
    if not os.path.exists(csv_file):
        print(f"Error: {csv_file} not found.")
        return

    with open(csv_file, mode='r', encoding='utf-8') as f:
        reader = list(csv.DictReader(f))

    total_staked = 0.0
    total_profit = 0.0
    games_evaluated = 0
    results_summary = {'ML': {'W': 0, 'L': 0, 'P': 0}, 'TOTAL': {'W': 0, 'L': 0, 'P': 0}}

    print(f"\n=======================================================")
    print(f"   MLB STRATEGY BACKTEST: {target_date} (FLAT ${FLAT_STAKE} STAKE)")
    print(f"=======================================================\n")

    for row in reader:
        # Date and Final Score Guards
        if target_date != 'ALL' and row.get('Date') != target_date:
            continue
        if row.get('Game_Status') != 'Final' or row.get('Actual_Total') in ['N/A', '', None]:
            continue

        games_evaluated += 1
        away = row['Away_Team']
        home = row['Home_Team']
        matchup = f"{away}@{home}"
        date = row.get('Date', '')

        # -------------------------------------------------------------
        # 1. MONEYLINE EVALUATION
        # -------------------------------------------------------------
        try:
            away_ev = float(row['Away_EV'])
            home_ev = float(row['Home_EV'])
            away_ml = float(row['Pinnacle_Away_ML'])
            home_ml = float(row['Pinnacle_Home_ML'])

            # Away Moneyline Bet
            if MIN_ML_EV <= away_ev <= MAX_ML_EV:
                stake = FLAT_STAKE
                profit, res = evaluate_bet('ML', 'away', row, stake)
                total_staked += stake
                total_profit += profit
                results_summary['ML'][res[0].upper()] += 1
                print(f"[{date}] BET ML: {away} ({away_ml:+.0f}) vs {home} | {res.upper()} | EV: +{away_ev:.1f}% | Stake: ${stake:.2f} | Profit: ${profit:+.2f}")

            # Home Moneyline Bet
            elif MIN_ML_EV <= home_ev <= MAX_ML_EV:
                stake = FLAT_STAKE
                profit, res = evaluate_bet('ML', 'home', row, stake)
                total_staked += stake
                total_profit += profit
                results_summary['ML'][res[0].upper()] += 1
                print(f"[{date}] BET ML: {home} ({home_ml:+.0f}) vs {away} | {res.upper()} | EV: +{home_ev:.1f}% | Stake: ${stake:.2f} | Profit: ${profit:+.2f}")

        except (ValueError, TypeError):
            pass

        # -------------------------------------------------------------
        # 2. TOTALS EVALUATION
        # -------------------------------------------------------------
        try:
            over_ev = float(row['Over_EV'])
            under_ev = float(row['Under_EV'])
            total_line = float(row['Pinnacle_Total_Line'])

            # Over Totals Bet
            if MIN_TOTAL_EV <= over_ev <= MAX_TOTAL_EV:
                stake = FLAT_STAKE
                profit, res = evaluate_bet('TOTAL', 'over', row, stake)
                total_staked += stake
                total_profit += profit
                results_summary['TOTAL'][res[0].upper()] += 1
                print(f"[{date}] BET TOTAL: OVER {total_line} ({matchup}) | {res.upper()} | EV: +{over_ev:.1f}% | Stake: ${stake:.2f} | Profit: ${profit:+.2f}")

            # Under Totals Bet (Optional toggle)
            elif PLAY_UNDERS and (MIN_TOTAL_EV <= under_ev <= MAX_TOTAL_EV):
                stake = FLAT_STAKE
                profit, res = evaluate_bet('TOTAL', 'under', row, stake)
                total_staked += stake
                total_profit += profit
                results_summary['TOTAL'][res[0].upper()] += 1
                print(f"[{date}] BET TOTAL: UNDER {total_line} ({matchup}) | {res.upper()} | EV: +{under_ev:.1f}% | Stake: ${stake:.2f} | Profit: ${profit:+.2f}")

        except (ValueError, TypeError):
            pass

    roi = (total_profit / total_staked * 100.0) if total_staked > 0 else 0.0
    total_bets = sum(results_summary['ML'].values()) + sum(results_summary['TOTAL'].values())

    print("\n-------------------------------------------------------")
    print("                 FINAL SUMMARY                         ")
    print("-------------------------------------------------------")
    print(f"Games Evaluated:   {games_evaluated}")
    print(f"Total Bets Placed: {total_bets}")
    print(f"Moneyline Record:  {results_summary['ML']['W']}-{results_summary['ML']['L']}-{results_summary['ML']['P']}")
    print(f"Totals Record:     {results_summary['TOTAL']['W']}-{results_summary['TOTAL']['L']}-{results_summary['TOTAL']['P']}")
    print(f"Total Staked:      ${total_staked:.2f}")
    print(f"Net Profit:        ${total_profit:+.2f}")
    print(f"Strategy ROI:      {roi:.2f}%")
    print("=======================================================\n")

if __name__ == '__main__':
    run_backtest(target_date='ALL')
