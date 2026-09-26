import csv
import os

# --- BACKTEST CONFIGURATION ---
FLAT_STAKE = 25.0

# Edge Hurdles (filters out low-conviction noise)
MIN_ML_EV = 5.0
MAX_ML_EV = 60.0

MIN_TOTAL_EV = 15.0        
MAX_TOTAL_EV = 60.0
PLAY_UNDERS = False        

def american_to_decimal(am_odds):
    try:
        odds = float(am_odds)
        if odds > 0:
            return (odds / 100.0) + 1.0
        else:
            return (100.0 / abs(odds)) + 1.0
    except (ValueError, TypeError):
        return 0.0

def evaluate_bet(market_type, pick, row, bet_amount):
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
        if actual_total == line:
            return 0.0, 'push'
        won = (actual_total > line) if pick == 'over' else (actual_total < line)
        profit = bet_amount * (100.0 / 110.0) if won else -bet_amount
        return profit, ('win' if won else 'loss')

def get_ev_bucket(ev):
    """Categorizes the EV into clean 5% increments for analysis."""
    lower = int(ev // 5) * 5
    upper = lower + 5
    return f"+{lower}% to +{upper}%"

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
    
    # Initialize trackers
    edge_buckets = {'ML': {}, 'TOTAL': {}}
    daily_stats = {}

    print(f"\n=======================================================")
    print(f"   MLB STRATEGY BACKTEST: {target_date} (FLAT ${FLAT_STAKE} STAKE)")
    print(f"=======================================================\n")

    for row in reader:
        if target_date != 'ALL' and row.get('Date') != target_date:
            continue
        if row.get('Game_Status') != 'Final' or row.get('Actual_Total') in ['N/A', '', None]:
            continue

        games_evaluated += 1
        away = row['Away_Team']
        home = row['Home_Team']
        matchup = f"{away}@{home}"
        date = row.get('Date', '')

        # 1. MONEYLINE EVALUATION
        try:
            away_ev = float(row['Away_EV'])
            home_ev = float(row['Home_EV'])
            away_ml = float(row['Pinnacle_Away_ML'])
            home_ml = float(row['Pinnacle_Home_ML'])

            bet_placed = False
            if MIN_ML_EV <= away_ev <= MAX_ML_EV:
                stake, profit, res, ev = FLAT_STAKE, *evaluate_bet('ML', 'away', row, FLAT_STAKE), away_ev
                team, odds = away, away_ml
                bet_placed = True
            elif MIN_ML_EV <= home_ev <= MAX_ML_EV:
                stake, profit, res, ev = FLAT_STAKE, *evaluate_bet('ML', 'home', row, FLAT_STAKE), home_ev
                team, odds = home, home_ml
                bet_placed = True

            if bet_placed:
                total_staked += stake
                total_profit += profit
                results_summary['ML'][res[0].upper()] += 1
                
                # Update Edge Buckets
                bucket = get_ev_bucket(ev)
                if bucket not in edge_buckets['ML']:
                    edge_buckets['ML'][bucket] = {'W': 0, 'L': 0, 'P': 0, 'Staked': 0.0, 'Profit': 0.0}
                edge_buckets['ML'][bucket][res[0].upper()] += 1
                edge_buckets['ML'][bucket]['Staked'] += stake
                edge_buckets['ML'][bucket]['Profit'] += profit

                # Update Daily Stats
                if date not in daily_stats:
                    daily_stats[date] = {'W': 0, 'L': 0, 'P': 0, 'Staked': 0.0, 'Profit': 0.0}
                daily_stats[date][res[0].upper()] += 1
                daily_stats[date]['Staked'] += stake
                daily_stats[date]['Profit'] += profit

                print(f"[{date}] BET ML: {team} ({odds:+.0f}) vs {'Opponent'} | {res.upper()} | EV: +{ev:.1f}% | Profit: ${profit:+.2f}")

        except (ValueError, TypeError):
            pass

        # 2. TOTALS EVALUATION
        try:
            over_ev = float(row['Over_EV'])
            under_ev = float(row['Under_EV'])
            total_line = float(row['Pinnacle_Total_Line'])

            bet_placed = False
            if MIN_TOTAL_EV <= over_ev <= MAX_TOTAL_EV:
                stake, profit, res, ev = FLAT_STAKE, *evaluate_bet('TOTAL', 'over', row, FLAT_STAKE), over_ev
                bet_type = 'OVER'
                bet_placed = True
            elif PLAY_UNDERS and (MIN_TOTAL_EV <= under_ev <= MAX_TOTAL_EV):
                stake, profit, res, ev = FLAT_STAKE, *evaluate_bet('TOTAL', 'under', row, FLAT_STAKE), under_ev
                bet_type = 'UNDER'
                bet_placed = True

            if bet_placed:
                total_staked += stake
                total_profit += profit
                results_summary['TOTAL'][res[0].upper()] += 1
                
                # Update Edge Buckets
                bucket = get_ev_bucket(ev)
                if bucket not in edge_buckets['TOTAL']:
                    edge_buckets['TOTAL'][bucket] = {'W': 0, 'L': 0, 'P': 0, 'Staked': 0.0, 'Profit': 0.0}
                edge_buckets['TOTAL'][bucket][res[0].upper()] += 1
                edge_buckets['TOTAL'][bucket]['Staked'] += stake
                edge_buckets['TOTAL'][bucket]['Profit'] += profit

                # Update Daily Stats
                if date not in daily_stats:
                    daily_stats[date] = {'W': 0, 'L': 0, 'P': 0, 'Staked': 0.0, 'Profit': 0.0}
                daily_stats[date][res[0].upper()] += 1
                daily_stats[date]['Staked'] += stake
                daily_stats[date]['Profit'] += profit

                print(f"[{date}] BET TOTAL: {bet_type} {total_line} ({matchup}) | {res.upper()} | EV: +{ev:.1f}% | Profit: ${profit:+.2f}")

        except (ValueError, TypeError):
            pass

    roi = (total_profit / total_staked * 100.0) if total_staked > 0 else 0.0
    total_bets = sum(results_summary['ML'].values()) + sum(results_summary['TOTAL'].values())

    print("\n=======================================================")
    print("                 FINAL SUMMARY                         ")
    print("=======================================================")
    print(f"Games Evaluated:   {games_evaluated}")
    print(f"Total Bets Placed: {total_bets}")
    print(f"Moneyline Record:  {results_summary['ML']['W']}-{results_summary['ML']['L']}-{results_summary['ML']['P']}")
    print(f"Totals Record:     {results_summary['TOTAL']['W']}-{results_summary['TOTAL']['L']}-{results_summary['TOTAL']['P']}")
    print(f"Total Staked:      ${total_staked:.2f}")
    print(f"Net Profit:        ${total_profit:+.2f}")
    print(f"Strategy ROI:      {roi:.2f}%")
    
    print("\n-------------------------------------------------------")
    print("           MONEYLINE PERFORMANCE BY EV TIER            ")
    print("-------------------------------------------------------")
    for bucket in sorted(edge_buckets['ML'].keys(), key=lambda x: float(x.split('%')[0].replace('+', ''))):
        b = edge_buckets['ML'][bucket]
        b_roi = (b['Profit'] / b['Staked'] * 100) if b['Staked'] > 0 else 0
        print(f" {bucket:<15} | {b['W']:>2}-{b['L']:>2}-{b['P']:>2} | Profit: ${b['Profit']:>7.2f} | ROI: {b_roi:>7.2f}%")

    print("\n-------------------------------------------------------")
    print("            TOTALS PERFORMANCE BY EV TIER              ")
    print("-------------------------------------------------------")
    for bucket in sorted(edge_buckets['TOTAL'].keys(), key=lambda x: float(x.split('%')[0].replace('+', ''))):
        b = edge_buckets['TOTAL'][bucket]
        b_roi = (b['Profit'] / b['Staked'] * 100) if b['Staked'] > 0 else 0
        print(f" {bucket:<15} | {b['W']:>2}-{b['L']:>2}-{b['P']:>2} | Profit: ${b['Profit']:>7.2f} | ROI: {b_roi:>7.2f}%")

    print("\n-------------------------------------------------------")
    print("                 DAILY PERFORMANCE                     ")
    print("-------------------------------------------------------")
    for date in sorted(daily_stats.keys()):
        d = daily_stats[date]
        d_roi = (d['Profit'] / d['Staked'] * 100) if d['Staked'] > 0 else 0.0
        print(f" {date} | {d['W']:>2}-{d['L']:>2}-{d['P']:>2} | Staked: ${d['Staked']:>6.2f} | Profit: ${d['Profit']:>7.2f} | ROI: {d_roi:>7.2f}%")
    print("=======================================================\n")

if __name__ == '__main__':
    run_backtest(target_date='ALL')
