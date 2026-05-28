"""
techndev-providers  pipelines  v0.1.0
=======================================
Plattformuebergreifende Orchestrierungs-Flows, die mehrere Provider mit der
reseller_profitability-Kalkulationslib verbinden.

Im Gegensatz zu den reinen Provider-Paketen (amazon_sp, ebay, …) importiert
dieses Paket reseller_profitability. Der Consumer muss daher sowohl providers/
als auch profitability/ (das Paket reseller_profitability) auf sys.path haben.

Oeffentliche API
----------------
  from pipelines.arbitrage import evaluate_arbitrage, ArbitrageResult
"""
from .arbitrage import evaluate_arbitrage, ArbitrageResult

__version__ = "0.1.0"

__all__ = [
    'evaluate_arbitrage',
    'ArbitrageResult',
]
