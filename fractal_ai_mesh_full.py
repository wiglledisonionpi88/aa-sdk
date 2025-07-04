import os, sys, time, threading, json, requests, traceback, subprocess
from datetime import datetime
import ccxt, numpy as np, pandas as pd

PAIRLIST = ["BTC/USDT","ETH/USDT","LINK/USDT","AVAX/USDT"]
MIN_TRADE = 10.0
PROFIT_TARGET = 0.0002
STOP_LOSS = -0.002
TIMEOUT = 600
GIT_PUSH_INTERVAL = 1800  # 30 min

# --- Check if local LLM is available (Ollama or Llama.cpp REST) ---
def detect_local_llm():
    try:
        # Ollama
        r = requests.get("http://localhost:11434/api/tags", timeout=2)
        if r.ok:
            print("[AI] Ollama local LLM detected.")
            return "ollama"
    except: pass
    try:
        # LM Studio/Llama.cpp REST API (default port)
        r = requests.get("http://localhost:1234/v1/models", timeout=2)
        if r.ok:
            print("[AI] Llama.cpp REST API detected.")
            return "llamacpp"
    except: pass
    print("[AI] No local LLM detected, using rule-based only.")
    return None

LLM_TYPE = detect_local_llm()

def query_local_llm(pair, context):
    if LLM_TYPE == "ollama":
        prompt = f"As a crypto trading AI, should I BUY, SELL or HOLD {pair} given {context}? Only answer BUY, SELL, or HOLD."
        r = requests.post(
            "http://localhost:11434/api/generate",
            json={"model":"llama3", "prompt":prompt, "stream":False, "options":{"temperature":0.1}}
        )
        res = r.json()
        return res.get("response", "HOLD").strip().upper()[:6]
    elif LLM_TYPE == "llamacpp":
        prompt = f"As a crypto trading AI, should I BUY, SELL or HOLD {pair} given {context}? Only answer BUY, SELL, or HOLD."
        r = requests.post(
            "http://localhost:1234/v1/chat/completions",
            json={"messages":[{"role":"user","content":prompt}]}
        )
        try:
            return r.json()["choices"][0]["message"]["content"].strip().upper()[:6]
        except: return "HOLD"
    return "HOLD"

# --- Rule-based fallback ---
def rule_decision(ticker):
    # Simple moving average/candle logic. Can be enhanced!
    try:
        close = float(ticker['close'])
        open_ = float(ticker['open'])
        pct = (close-open_)/open_
        if pct > PROFIT_TARGET: return "BUY"
        if pct < STOP_LOSS: return "SELL"
        return "HOLD"
    except: return "HOLD"

def mesh_ai_cycle():
    ex = ccxt.kucoin({
        "apiKey": os.getenv("KUCOIN_API_KEY"),
        "secret": os.getenv("KUCOIN_API_SECRET"),
        "password": os.getenv("KUCOIN_API_PASSPHRASE"),
    })
    open_orders = {}
    trade_log = []
    while True:
        ex.load_markets()
        for pair in PAIRLIST:
            try:
                t = ex.fetch_ticker(pair)
                bal = ex.fetch_balance().get("USDT", {}).get("free", 0)
                if bal < MIN_TRADE: continue
                qty = round(MIN_TRADE / float(t["ask"]), 6)
                context = f"Current ask: {t['ask']}, USDT balance: {bal}, open: {t['open']}, close: {t['close']}"
                # Decision: Local LLM + rule-based, fallback to rule-based if LLM unavailable
                ai_decision = "HOLD"
                if LLM_TYPE:
                    ai_decision = query_local_llm(pair, context)
                if ai_decision not in ("BUY","SELL","HOLD"):
                    ai_decision = rule_decision(t)
                print(f"[AI] Decision for {pair}: {ai_decision}")
                # Feedback/correction: If 3 losses in last 5, switch to HOLD
                recent = [x["pnl"] for x in trade_log[-5:] if "pnl" in x]
                if len([x for x in recent if x<0]) >= 3:
                    ai_decision = "HOLD"
                if ai_decision == "BUY" and (ex.id, pair) not in open_orders:
                    print(f"[AI] BUY {qty} {pair} @ {t['ask']}")
                    order = ex.create_market_buy_order(pair, qty)
                    open_orders[(ex.id, pair)] = {"price": float(t['ask']), "qty": qty, "time": time.time()}
                elif ai_decision == "SELL" and (ex.id, pair) in open_orders:
                    cur_price = float(t["bid"])
                    entry = open_orders[(ex.id, pair)]
                    pnl = (cur_price - entry["price"]) / entry["price"]
                    print(f"[AI] SELL {entry['qty']} {pair} @ {cur_price} | PNL={pnl*100:.3f}%")
                    order = ex.create_market_sell_order(pair, entry["qty"])
                    trade_log.append({"pair": pair, "action": "SELL", "pnl": pnl, "time": datetime.now().isoformat()})
                    del open_orders[(ex.id, pair)]
                else:
                    print(f"[AI] HOLD {pair}")
            except Exception as e:
                print(f"[AI-ERR] {pair}: {e}")
                traceback.print_exc()
            time.sleep(2)
        time.sleep(TIMEOUT//len(PAIRLIST))

# === Flask Dashboard ===
from flask import Flask, jsonify
from flask_cors import CORS
app = Flask(__name__)
CORS(app)
@app.route("/ai_status")
def ai_status():
    return jsonify({"time": datetime.now().isoformat(), "status": "AI mesh running."})

def dash_thread(): app.run(port=8181, host="0.0.0.0")

# === GitHub Auto-Sync ===
def github_push_loop():
    repo_url = os.getenv("GITHUB_REPO")
    if not repo_url: return
    if not os.path.isdir("$HOME/aa-sdk"):
        os.system(f"git clone https://{os.getenv('GITHUB_USER')}:{os.getenv('GITHUB_PAT')}@{repo_url} $HOME/aa-sdk || true")
    repo_dir = os.path.expanduser("~/aa-sdk")
    while True:
        try:
            os.system(f"cp $HOME/fractal_ai_mesh_full.py $HOME/aa-sdk/")
            os.chdir(repo_dir)
            os.system("git add .")
            os.system(f"git commit -am 'Automated AI mesh update {datetime.now().isoformat()}' || true")
            os.system("git pull --rebase")
            os.system("git push || true")
        except Exception as e:
            print(f"[GIT-ERR] {e}")
        time.sleep(GIT_PUSH_INTERVAL)

if __name__ == "__main__":
    t1 = threading.Thread(target=mesh_ai_cycle, daemon=True)
    t2 = threading.Thread(target=dash_thread, daemon=True)
    t3 = threading.Thread(target=github_push_loop, daemon=True)
    t1.start(); t2.start(); t3.start()
    t1.join(); t2.join(); t3.join()
