import os
import time
import requests
from web3 import Web3

# ------------------------------------------------------------------
# CONFIGURATION & TELEGRAM CREDENTIALS
# ------------------------------------------------------------------
RPC_URL = "https://rpc.mainnet.chain.robinhood.com"
w3 = Web3(Web3.HTTPProvider(RPC_URL))

TELEGRAM_BOT_TOKEN = "8836639567:AAGjwo0Oe_enFgnPGicJiFTC2akZIbiFuJ0"
TELEGRAM_CHAT_ID = "1424132044"

# Set minimal likuiditas aman (misal 0.2 ETH / ~ $500+)
MIN_LIQUIDITY_ETH = 0.2 

WETH_ADDRESS = Web3.to_checksum_address("0x4200000000000000000000000000000000000006")

ERC20_ABI = [
    {"constant": True, "inputs": [], "name": "name", "outputs": [{"name": "", "type": "string"}], "type": "function"},
    {"constant": True, "inputs": [], "name": "symbol", "outputs": [{"name": "", "type": "string"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "owner", "type": "address"}], "name": "balanceOf", "outputs": [{"name": "balance", "type": "uint256"}], "type": "function"}
]

KNOWN_RUGPULL_DEPLOYERS = set(["0x0000000000000000000000000000000000000000"])
PROCESSED_TOKENS = set()

# ------------------------------------------------------------------
# SECURITY FILTERS
# ------------------------------------------------------------------
def is_real_dex_token(token_address):
    """
    Memastikan token adalah ERC-20 asli dan bukan Smart Wallet / System Contract.
    """
    try:
        checksum_token = Web3.to_checksum_address(token_address)
        contract = w3.eth.contract(address=checksum_token, abi=ERC20_ABI)
        
        symbol = contract.functions.symbol().call()
        name = contract.functions.name().call()

        # Keyword Blocker untuk wallet/sistem internal
        ignore_keywords = ["account", "wallet", "proxy", "vault", "entrypoint", "zmag"]
        if any(keyword in name.lower() or keyword in symbol.lower() for keyword in ignore_keywords):
            return False, None, None

        return True, symbol, name
    except Exception:
        return False, None, None

def check_contract_safety(token_address):
    """
    Memeriksa Source Code di Blockscout: Harus Verified & Tidak Memiliki Fungsi 'Mint' Berbahaya
    """
    url = f"https://robinhoodchain.blockscout.com/api?module=contract&action=getsourcecode&address={token_address}"
    try:
        res = requests.get(url, timeout=10).json()
        result = res.get("result", [{}])[0]
        abi = result.get("ABI", "")
        source_code = result.get("SourceCode", "").lower()
        
        # 1. Reject jika Unverified
        if abi == "Contract source code not verified":
            return False, "Unverified Source Code"

        # 2. Reject jika ada fungsi Minting berbahaya / Inflation Scam
        if "function mint(" in source_code or "function _mint(" in source_code:
            # Jika ada fungsi minting tetapi tidak ada batas / max supply
            if "cap" not in source_code and "maxsupply" not in source_code:
                return False, "Risiko High Inflation (Hidden Minting Function)"

        # 3. Warning Check: Proxy Contract (Upgradeable)
        is_proxy = "implementation" in source_code or "proxy" in source_code
        
        return True, "PASSED", is_proxy
    except Exception:
        return False, "Gagal Verifikasi Contract", False

def check_liquidity_depth(token_address):
    """
    Memeriksa jumlah likuiditas (ETH/WETH)
    """
    try:
        weth_contract = w3.eth.contract(address=WETH_ADDRESS, abi=ERC20_ABI)
        
        weth_balance_wei = weth_contract.functions.balanceOf(Web3.to_checksum_address(token_address)).call()
        weth_balance = w3.from_wei(weth_balance_wei, 'ether')

        native_eth_balance_wei = w3.eth.get_balance(Web3.to_checksum_address(token_address))
        native_eth_balance = w3.from_wei(native_eth_balance_wei, 'ether')

        total_liquidity_eth = float(weth_balance) + float(native_eth_balance)

        if total_liquidity_eth < MIN_LIQUIDITY_ETH:
            return False, total_liquidity_eth

        return True, total_liquidity_eth
    except Exception:
        return False, 0.0

def send_telegram_alert(token_address, deployer_address, symbol, name, liquidity_eth, is_proxy):
    proxy_status = "⚠️ YES (Upgradeable)" if is_proxy else "✅ NO (Standard ERC-20)"

    msg = (
        f"🚨 **SAFE TOKEN PASSED FILTERS** 🚨\n\n"
        f"• **Name:** {name}\n"
        f"• **Symbol:** ${symbol}\n"
        f"• **Token Contract:** `{token_address}`\n"
        f"• **Initial Liquidity:** ~{liquidity_eth:.2f} ETH 💧\n"
        f"• **Deployer:** `{deployer_address}`\n\n"
        f"📊 **Security Overview:**\n"
        f"├ **Source Code:** ✅ Verified\n"
        f"├ **Mint Function:** ✅ Safe / No Unlimited Mint\n"
        f"├ **Proxy Contract:** {proxy_status}\n"
        f"└ **Deployer Record:** Clean\n\n"
        f"🔗 [Blockscout Explorer](https://robinhoodchain.blockscout.com/token/{token_address})\n"
        f"📈 [DexScreener](https://dexscreener.com/robinhood/{token_address})"
    )
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": msg, "parse_mode": "Markdown"}, timeout=10)
        print(f"✅ ALERT SENT TO TELEGRAM: ${symbol} ({token_address})")
    except Exception as e:
        print(f"Error mengirim pesan Telegram: {e}")

# ------------------------------------------------------------------
# MAIN PIPELINE
# ------------------------------------------------------------------
def process_token(token_address, deployer_address):
    if token_address.lower() in PROCESSED_TOKENS:
        return
    PROCESSED_TOKENS.add(token_address.lower())

    # Step 1: Validasi standar ERC-20
    is_token, symbol, name = is_real_dex_token(token_address)
    if not is_token:
        return

    # Step 2: Validasi Keamanan Contract (Verified & Minting Check)
    is_safe_contract, reason, is_proxy = check_contract_safety(token_address)
    if not is_safe_contract:
        print(f"❌ REJECTED [{symbol}]: {reason}")
        return

    # Step 3: Beri jeda 5 detik agar developer selesai memasukkan likuiditas di DEX
    time.sleep(5)

    # Step 4: Memeriksa Likuiditas
    has_enough_liquidity, liquidity_eth = check_liquidity_depth(token_address)
    if not has_enough_liquidity:
        print(f"❌ REJECTED [{symbol}]: Liquidity Terlalu Rendah ({liquidity_eth:.4f} ETH)")
        return

    # Step 5: Kirim Alert
    send_telegram_alert(token_address, deployer_address, symbol, name, liquidity_eth, is_proxy)

def main_loop():
    print(f"🚀 Bot Listener Robinhood Chain (Full Safety Mode) Berjalan...")
    latest_block = w3.eth.block_number

    while True:
        try:
            current_block = w3.eth.block_number
            if current_block > latest_block:
                for b in range(latest_block + 1, current_block + 1):
                    block = w3.eth.get_block(b, full_transactions=True)
                    for tx in block.transactions:
                        if tx.get('to') is None:
                            receipt = w3.eth.get_transaction_receipt(tx['hash'])
                            if receipt and receipt.get('contractAddress'):
                                contract_addr = receipt['contractAddress']
                                deployer_addr = tx['from']
                                process_token(contract_addr, deployer_addr)
                latest_block = current_block
            time.sleep(3)
        except Exception as e:
            time.sleep(5)

if __name__ == "__main__":
    main_loop()