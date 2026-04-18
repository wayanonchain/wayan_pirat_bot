"""
Narrative classifier + competitor database.
Classifies token into a sector/narrative by name, symbol, description keywords.
Returns: narrative name, description, top competitors, why it matters.
"""
from dataclasses import dataclass
from typing import Optional

# ─────────────────────────────────────────────────────────────────────────────
# Competitor database — top tokens by narrative (hardcoded, no API needed)
# ─────────────────────────────────────────────────────────────────────────────

NARRATIVES: dict[str, dict] = {
    "ai_agents": {
        "name": "AI Agents",
        "description": "Автономные AI-агенты на блокчейне. Тренд: команды строят агентов, которые могут торговать, управлять DAOs, взаимодействовать с DeFi.",
        "why_matters": "Один из самых горячих нарративов 2024–2025. Coinbase, a16z активно инвестируют. Ожидается волна новых агентских протоколов.",
        "competitors": ["VIRTUAL", "AI16Z", "AIXBT", "ARC", "ZEREBRO", "AGENT", "ELIZA", "GRIFFAIN"],
        "keywords": ["agent", "ai agent", "autonomous", "eliza", "virtuals", "arc", "zerebro", "intelligen", "cognitive"],
    },
    "ai_infra": {
        "name": "AI Infrastructure",
        "description": "Инфраструктура для AI: децентрализованные GPU, датасеты, обучение моделей, хранилища.",
        "why_matters": "Спрос на GPU compute растёт экспоненциально. Децентрализованные альтернативы AWS/Azure дешевле.",
        "competitors": ["RENDER", "AKT", "IO", "TAO", "NGL", "GRASS", "PRIME", "NOSANA", "GPU"],
        "keywords": ["gpu", "compute", "render", "inference", "training", "model", "llm", "neural", "bittensor", "federated", "decentralized ai", "deai"],
    },
    "depin": {
        "name": "DePIN",
        "description": "Decentralized Physical Infrastructure Networks — интернет, 5G, хранилища, сенсоры, IoT.",
        "why_matters": "Реальная полезность + token incentives. Helium доказал модель. Следующая волна — энергетика, карты, датчики.",
        "competitors": ["HNT", "MOBILE", "IOTX", "DIMO", "NATIX", "HIVEMAPPER", "WLD", "GEODNET", "WIFI"],
        "keywords": ["wireless", "network", "5g", "iot", "sensor", "infrastructure", "hotspot", "helium", "bandwidth", "coverage", "connectivity", "depin", "physical"],
    },
    "rwa": {
        "name": "Real World Assets (RWA)",
        "description": "Токенизация реальных активов: недвижимость, гособлигации, кредиты, товары.",
        "why_matters": "BlackRock, Franklin Templeton уже в RWA. Ожидаемый рынок — $10+ трлн к 2030. Регуляторная ясность приближается.",
        "competitors": ["ONDO", "MKR", "BACKED", "POLYMATH", "TRU", "MPL", "CENTRIFUGE", "GOLDFINCH", "PLUME"],
        "keywords": ["real world", "rwa", "tokenize", "treasury", "bond", "estate", "property", "credit", "commodity", "gold", "silver", "yield", "t-bill"],
    },
    "meme": {
        "name": "Meme Coin",
        "description": "Спекулятивный мем-токен. Стоимость определяется нарративом, комьюнити и вирусностью.",
        "why_matters": "Мемы — двигатель ликвидности в крипте. Хорошо подобранный мем может дать 100x быстрее любого фундаментала.",
        "competitors": ["DOGE", "SHIB", "PEPE", "WIF", "BONK", "POPCAT", "MEW", "BRETT", "FLOKI", "MOG"],
        "keywords": ["dog", "cat", "pepe", "frog", "meme", "fun", "coin", "inu", "shiba", "doge", "moon", "rocket", "wagmi", "gm", "cute", "baby", "elon", "wif", "bonk"],
    },
    "gamefi": {
        "name": "GameFi / Play-to-Earn",
        "description": "Блокчейн-игры с токен-экономикой: P2E, NFT предметы, игровые токены.",
        "why_matters": "Gaming — крупнейшая развлекательная индустрия. Web3 gaming привлекает крупные студии.",
        "competitors": ["AXS", "SAND", "MANA", "ILV", "GOD", "RON", "BEAM", "MAGIC", "GALA", "YGG"],
        "keywords": ["game", "gaming", "play", "earn", "guild", "nft", "metaverse", "virtual world", "rpg", "quest", "character", "item", "loot", "esport"],
    },
    "socialfi": {
        "name": "SocialFi",
        "description": "Децентрализованные социальные сети с токен-монетизацией для создателей и пользователей.",
        "why_matters": "Creator economy + crypto = новая бизнес-модель. Альтернатива цензуре Web2 платформ.",
        "competitors": ["FRIEND", "DeSo", "LENS", "CYBER", "FARCASTER", "ORBS", "STEEM", "HIVE"],
        "keywords": ["social", "creator", "influencer", "community", "content", "follow", "post", "message", "chat", "friend"],
    },
    "liquid_staking": {
        "name": "Liquid Staking",
        "description": "Ликвидные деривативы на застейканные активы: ETH, SOL, AVAX. Используются в DeFi как залог.",
        "why_matters": "LST — крупнейший сегмент DeFi. После Ethereum merge взрывной рост. Solana LST ещё только начинается.",
        "competitors": ["LDO", "RPL", "JITO", "MSOL", "BSOL", "FRXETH", "SFRXETH", "ANKR", "SWISE"],
        "keywords": ["liquid staking", "staking", "lido", "jito", "restake", "beacon", "validator", "liquid", "derivative", "lst", "lrt"],
    },
    "derivatives": {
        "name": "Derivatives / Perpetuals",
        "description": "Децентрализованные биржи деривативов: перпетуалы, опционы, синтетика.",
        "why_matters": "Объём деривативов на CEX 10x выше спота. DEX деривативы — следующий фронтир.",
        "competitors": ["GMX", "DYDX", "SNX", "PERP", "DRIFT", "MUX", "JUP", "HYPERLIQUID", "AEVO"],
        "keywords": ["perp", "perpetual", "derivative", "leverage", "margin", "short", "long", "futures", "options", "synthetic", "position"],
    },
    "dex": {
        "name": "DEX / AMM",
        "description": "Децентрализованные биржи и автоматические маркет-мейкеры.",
        "why_matters": "Фундаментальная инфраструктура DeFi. Накапливают комиссии и ликвидность.",
        "competitors": ["UNI", "CAKE", "SUSHI", "ORCA", "RAY", "METEORA", "CURVE", "BALANCER", "VELODROME"],
        "keywords": ["swap", "dex", "amm", "liquidity", "pool", "exchange", "market maker", "aggregator"],
    },
    "lending": {
        "name": "Lending / Borrowing",
        "description": "Протоколы кредитования: залоговые займы, флэш-лоны, undercollateralized.",
        "why_matters": "Базовый слой DeFi. Генерирует реальную выручку через процентные ставки.",
        "competitors": ["AAVE", "COMP", "MORPHO", "KAMINO", "SOLEND", "EULER", "RADIANT", "BENQI"],
        "keywords": ["lend", "borrow", "loan", "collateral", "interest", "rate", "supply", "credit"],
    },
    "bridge": {
        "name": "Cross-chain / Bridge",
        "description": "Межсетевые мосты и протоколы обмена ликвидностью между блокчейнами.",
        "why_matters": "Мультичейн будущее требует надёжных бриджей. После взломов рынок консолидируется вокруг надёжных решений.",
        "competitors": ["WORMHOLE", "LAYERZERO", "STARGATE", "ACROSS", "SYNAPSE", "HOP", "CELER"],
        "keywords": ["bridge", "cross-chain", "interchain", "interoperability", "wormhole", "layerzero", "relay", "omni"],
    },
    "privacy": {
        "name": "Privacy",
        "description": "Протоколы конфиденциальности: приватные транзакции, ZK-доказательства, шифрование.",
        "why_matters": "Регуляторное давление усиливает спрос на приватность. ZK технологии — горячий тренд.",
        "competitors": ["XMR", "ZEC", "SCRT", "ROSE", "BEAM", "DERO", "RAILGUN", "TORNADO"],
        "keywords": ["privacy", "private", "anonymous", "confidential", "zero knowledge", "zk", "stealth", "shield", "encrypt"],
    },
    "layer1": {
        "name": "Layer 1 / Base Chain",
        "description": "Базовый блокчейн: консенсус, виртуальная машина, нативная экосистема.",
        "why_matters": "L1 накапливают сетевые эффекты. Новые L1 нишуются (SVM, MoveVM) вместо конкуренции с ETH.",
        "competitors": ["SOL", "AVAX", "SUI", "APT", "SEI", "MONAD", "BERACHAIN", "NEAR", "TON"],
        "keywords": ["layer 1", "l1", "blockchain", "consensus", "mainnet", "validator", "chain", "evm", "svm", "move vm"],
    },
    "layer2": {
        "name": "Layer 2 / Rollup",
        "description": "Решения масштабируемости Ethereum: optimistic rollups, ZK rollups.",
        "why_matters": "ETH scaling roadmap. L2 TVL растёт, экосистемы развиваются вокруг ARB, OP, BASE.",
        "competitors": ["ARB", "OP", "MATIC", "STRK", "MANTA", "SCROLL", "ZKSYNC", "LINEA", "BLAST"],
        "keywords": ["layer 2", "l2", "rollup", "zk rollup", "optimistic", "arbitrum", "optimism", "polygon", "scaling"],
    },
    "oracle": {
        "name": "Oracle / Data Feed",
        "description": "Оракулы: внешние данные для смарт-контрактов (цены, рандом, погода, спорт).",
        "why_matters": "Критическая инфраструктура DeFi. Без оракулов нет lending, derivatives, prediction markets.",
        "competitors": ["LINK", "PYTH", "BAND", "API3", "DIA", "UMA", "TELLOR", "REDSTONE"],
        "keywords": ["oracle", "data feed", "price feed", "chainlink", "pyth", "random", "vrf", "off-chain data"],
    },
    "prediction": {
        "name": "Prediction Markets",
        "description": "Рынки предсказаний: ставки на исходы событий (выборы, спорт, цены).",
        "why_matters": "Выборы 2024 показали потенциал. Polymarket достиг $1B+ объёма.",
        "competitors": ["POLY", "GNO", "TRUTH", "DRIFT", "MANIFOLD", "AZURO"],
        "keywords": ["predict", "prediction", "market", "election", "outcome", "bet", "odds", "forecast"],
    },
    "nft_infra": {
        "name": "NFT Infrastructure",
        "description": "Инфраструктура для NFT: маркетплейсы, launchpads, royalty enforcement, IP.",
        "why_matters": "NFT market consolidating. New primitives: IP tokenization, digital identity.",
        "competitors": ["BLUR", "LOOKS", "X2Y2", "MAGIC EDEN", "TENSOR", "STORY"],
        "keywords": ["nft", "art", "collectible", "marketplace", "royalty", "mint", "generative", "pfp", "ip"],
    },
    "stablecoin": {
        "name": "Stablecoin / Yield",
        "description": "Алгоритмические или обеспеченные стейблкоины, протоколы yield-стейблов.",
        "why_matters": "Стейблы — фундамент крипто-экономики. Yield-bearing stables (USDe, sDAI) — новый тренд.",
        "competitors": ["FRAX", "LUSD", "CRVUSD", "USDE", "GHO", "RAI", "SPELL", "SFRAX"],
        "keywords": ["stable", "stablecoin", "usd", "peg", "collateral", "cdp", "algorithmic", "yield bearing"],
    },
    "dao": {
        "name": "DAO / Governance",
        "description": "Протоколы управления, voting, treasury management.",
        "why_matters": "On-chain governance становится стандартом. Treasury management DAOs управляют млрд в DeFi.",
        "competitors": ["ENS", "ARB", "UNI", "AAVE", "GITCOIN", "SNAPSHOT", "SAFE"],
        "keywords": ["dao", "governance", "vote", "proposal", "treasury", "multisig", "community"],
    },
}

# ─────────────────────────────────────────────────────────────────────────────
# Classifier
# ─────────────────────────────────────────────────────────────────────────────

@dataclass
class NarrativeResult:
    narrative_key: str
    name: str
    description: str
    why_matters: str
    competitors: list[str]
    confidence: str  # "high" | "medium" | "low"
    match_reason: str


def classify_narrative(
    symbol: str,
    name: str,
    description: str = "",
    categories: list[str] = None,
) -> NarrativeResult:
    """
    Classify token into a narrative.
    Checks: symbol, name, description, categories.
    Returns best match.
    """
    if categories is None:
        categories = []

    text = " ".join([
        symbol.lower(),
        name.lower(),
        description.lower(),
        " ".join(categories).lower(),
    ])

    scores: dict[str, float] = {}

    for key, data in NARRATIVES.items():
        score = 0.0
        matched_kw = []
        for kw in data["keywords"]:
            if kw in text:
                weight = 2.0 if kw in symbol.lower() or kw in name.lower() else 1.0
                score += weight
                matched_kw.append(kw)
        if score > 0:
            scores[key] = score

    if not scores:
        return NarrativeResult(
            narrative_key="unknown",
            name="Неизвестный нарратив",
            description="Токен не попал ни в одну известную категорию.",
            why_matters="Требует ручного анализа.",
            competitors=[],
            confidence="low",
            match_reason="Ключевые слова не найдены",
        )

    best_key = max(scores, key=lambda k: scores[k])
    best     = NARRATIVES[best_key]
    best_score = scores[best_key]

    # Also check coingecko categories for override
    for cat in categories:
        cat_l = cat.lower()
        for key, data in NARRATIVES.items():
            for kw in data["keywords"]:
                if kw in cat_l and key not in scores:
                    scores[key] = 0.5

    confidence = "high" if best_score >= 3 else ("medium" if best_score >= 1.5 else "low")

    matched_kws = [kw for kw in best["keywords"] if kw in text]
    match_reason = f"Совпадения: {', '.join(matched_kws[:4])}"

    # Remove the token's own symbol from competitors list
    competitors = [c for c in best["competitors"] if c.upper() != symbol.upper()]

    return NarrativeResult(
        narrative_key=best_key,
        name=best["name"],
        description=best["description"],
        why_matters=best["why_matters"],
        competitors=competitors,
        confidence=confidence,
        match_reason=match_reason,
    )
