# PHANTOM_GMGN_COMPATIBILITY — Phantom, wallet de trading GMGN et Agent API

> Décision du propriétaire (D-009) : le bot trade avec le **wallet de trading intégré à GMGN**, alimenté manuellement. Phantom sert seulement à se connecter à GMGN et à envoyer les fonds.

## 1. Ce qui est établi

| Fait | Source |
|---|---|
| Un swap se fait avec `--from <wallet>`, qui doit être un wallet **lié à la clé API** | `gmgn-skills/docs/cli-usage.md` (swap : « must match the wallet bound to the API Key ») |
| `portfolio info` renvoie `{"wallets":[{"chain":…, "address":…}, …]}` | Démo `app.py` L357-372 (code exécuté contre l'API réelle) + réponse démo `{"wallets":[]}` observée en Phase 1 |
| `GMGN_PRIVATE_KEY` = clé Ed25519 de **signature de requêtes**, pas une clé de wallet | `.env.example` officiel ; démo README (« an Ed25519 PEM signing key, not a wallet private key ») |
| La transaction on-chain est donc signée **côté GMGN** pour le wallet lié | Conséquence des deux points précédents |
| La clé API est créée sur `gmgn.ai/ai` avec la clé publique **et une IP sortante** ; elle est liée à cette whitelist IP et ne se partage pas | Démo README (« Setup » §3) ; README officiel : whitelist requise pour le trading |
| IPv4 uniquement | README officiel (dépannage 401/403) |
| La démo a été vérifiée en achat/vente réels sur Solana avec ce modèle | Démo README (« Known limitations ») |

## 2. Ce qui reste à confirmer

| Question | Pourquoi c'est important | Comment le vérifier |
|---|---|---|
| Q-A : Le wallet lié est-il l'adresse Phantom ou un wallet de trading GMGN distinct ? | Détermine où déposer les fonds | `check_wallet_binding.py` + comparaison avec l'adresse affichée sur GMGN |
| Q-B : La whitelist IP s'applique-t-elle aussi aux appels en lecture ? | Ton IP résidentielle peut changer → coupure de la Phase 2 | Créer la clé avec ton IP actuelle ; noter le comportement si l'IP change (erreur 401/403) |
| Q-C : Peut-on modifier la whitelist d'une clé, ou faut-il une nouvelle clé pour le VPS ? | Passage du PC au VPS en Phase 6–7 | Interface gmgn.ai/ai ; prévoir une **seconde clé** dédiée au VPS |
| Q-D : Comment retirer les fonds du wallet de trading GMGN ? Qui détient sa clé ? | Garde des fonds (Q-002) | Documentation GMGN / support Telegram `t.me/gmgnagentapi` ; tester un retrait de 1 $ avant d'alimenter 50 $ |
| Q-E : Que se passe-t-il pour les ordres conditionnels si la clé API est révoquée ? | Kill switch externe | À vérifier en Phase 6 (créer puis révoquer une clé de test sans position) |
| Q-F : Frais GMGN par swap | Rentabilité sur 5 $ (Q-003) | Lire `report` d'un swap de test en Phase 6 |

## 3. Arbre de décision

```
portfolio info avec TA clé
├─ aucun wallet SOL ................................. CAS C → STOP, rapport
├─ wallet SOL ≠ adresse attendue ...................... STOP, vérifier sur gmgn.ai
└─ wallet SOL = wallet de trading GMGN attendu ........ CAS B confirmé
     ├─ retrait test de 1 $ réussi (Q-D) .............. OK pour alimenter (≤ 50 $)
     └─ retrait impossible / flou ..................... STOP avant tout dépôt
```

## 4. Procédure (rien de secret ne sort de ta machine)

1. `python scripts/check_wallet_binding.py --demo` → connectivité.
2. `python scripts/generate_signing_key.py` → colle la **clé publique** sur gmgn.ai/ai, avec ton IP actuelle ; **n'active pas le trading**.
3. `python scripts/generate_signing_key.py --set-api-key`.
4. Repère sur GMGN l'adresse publique de ton wallet de trading.
5. `python scripts/check_wallet_binding.py --expect <adresse>` → envoie-moi la sortie.
6. Avant tout dépôt réel : vérifier Q-D (retrait test).

## 5. Points de vigilance tirés de la démo

- La démo choisit **automatiquement** le premier wallet SOL lié. Nous imposons l'adresse configurée (`GMGN_TRADING_WALLET`) et refusons de démarrer en cas d'écart.
- Ne lance **pas** la démo en mode LIVE avec ta clé : `LIVE_TRADING_DISABLED = False` dans le dépôt, et l'achat utilise un slippage probablement erroné (B1). Avec une clé **lecture seule**, le risque est nul : les ordres seraient refusés.
- La démo envoie les clés par le navigateur (`/api/config`). Utilise plutôt nos scripts, qui écrivent directement dans `~/.config/gmgn/.env`.

## 6. Conclusion

Le modèle « wallet de trading lié à la clé API, signature côté GMGN » est **compatible avec l'exécution autonome**. La démo le prouve en pratique sur Solana. Aucune clé de wallet ni phrase de récupération n'est nécessaire. Il reste à confirmer **ton** wallet (Q-A), la garde et le retrait des fonds (Q-D) et la gestion IP pour le VPS (Q-B, Q-C).
