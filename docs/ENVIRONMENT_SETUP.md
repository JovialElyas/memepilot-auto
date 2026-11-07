# ENVIRONMENT_SETUP — Environnement de développement (Windows 11)

État de référence au 2026-10-31.

## 1. État actuel

| Composant | Valeur | Statut |
|---|---|---|
| OS / shell | Windows 11, PowerShell | ✔ |
| Python | 3.12.10 via le lanceur `py` | ✔ (≥ 3.11 requis) |
| Environnement virtuel | `memepilot-auto\.venv`, préfixe `(.venv)` visible | ✔ |
| Node.js / npm | installés | ✔ |
| gmgn-cli | **1.7.1**, global, `%APPDATA%\npm\gmgn-cli.cmd` | ✔ (version épinglée du projet) |
| Nom de la commande | `gmgn-cli` (pas `gmgn`) | ✔ |
| Dépendances du projet (`pip install -e ".[dev]"`) | **non mentionné** | ⏳ |
| Tests (`pytest`) sous Windows | **non mentionné** | ⏳ |
| Clé API GMGN | non configurée (voulu) | ⏳ Phase 1 |

**Ne rien réinstaller** : Python, Node et gmgn-cli sont bons.

## 2. Règles de commande sous Windows

| Linux / doc | Windows PowerShell |
|---|---|
| `python3` | `py` (hors venv) ou `python` (venv actif) |
| `source .venv/bin/activate` | `.\.venv\Scripts\Activate.ps1` |
| `pip install …` | `python -m pip install …` (venv actif) |
| `gmgn` | `gmgn-cli` |

### Point à vérifier : « pip introuvable » dans le venv
Si le venv est vraiment actif, `pip` devrait exister. Vérifie que c'est bien le Python du venv qui tourne :
```powershell
python -c "import sys; print(sys.prefix)"
# attendu : <dossier du projet>\.venv
```
Si le chemin affiché n'est pas `.venv`, réactive le venv. Avec le venv actif, `py -m pip` utilise bien le venv, mais `py -3.12 -m pip` (version précisée) viserait le Python global. Pour éviter toute ambiguïté, préfère `python -m pip`.

## 3. Étapes restantes (à faire dans l'ordre)

```powershell
cd $HOME\Downloads\memepilot-auto
.\.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
python -m pytest              # attendu : 47 passed
python -m ruff check .
python -m mypy
python scripts\check_wallet_binding.py --demo
```

Si un test échoue sous Windows, envoie-moi la sortie complète de `pytest` (aucun secret n'y figure). Les tests du client lancent un faux `gmgn-cli` (`.cmd` sous Windows) : c'est le premier vrai test de notre client sur ta machine.

## 4. Configuration des identifiants GMGN (Phase 1)

Il existe deux méthodes. Les deux gardent la clé privée sur ta machine.

| | **A. Commande officielle `gmgn-cli config`** | **B. Notre script** |
|---|---|---|
| Génération | `gmgn-cli config` → crée `~/.config/gmgn/keypair.pem` (privée + publique) et affiche un **lien gmgn.ai pré-rempli** avec ta clé publique | `python scripts\generate_signing_key.py` → affiche la clé publique à coller |
| Enregistrement de la clé API | `gmgn-cli config --apply <API_KEY>` → écrit `.env` puis vérifie la clé | `python scripts\generate_signing_key.py --set-api-key` (saisie masquée) |
| Inconvénient | La clé API est tapée en clair dans la commande → elle finit dans l'**historique PowerShell** (`PSReadLine`). La clé privée existe en **deux copies** (`keypair.pem` + `.env`) | Pas de lien pré-rempli |
| Message affiché | « send me the API Key » (texte prévu pour un agent IA) → **ne l'envoie à personne, ni à moi** | — |

**Recommandation : méthode A pour le lien pré-rempli, puis l'une de ces deux options :**
- `gmgn-cli config --apply <API_KEY>`, puis efface la ligne de l'historique :
  `Remove-Item (Get-PSReadLineOption).HistorySavePath` (efface tout l'historique PowerShell), **ou**
- je fais évoluer notre `--set-api-key` pour lire `keypair.pem` (petite modification, après ton feu vert).

Dans tous les cas, sur gmgn.ai : **ne pas activer le trading** sur cette clé pour l'instant.

## 5. Hygiène

- `~/.config/gmgn/` est dans ton profil Windows : ne le synchronise pas avec OneDrive.
- `.gitignore` exclut déjà `.env`, `*.pem`, `*.db`.
- Si tu crées un dépôt GitHub : **privé**, puis déplace `ci/github-ci.yml` vers `.github/workflows/ci.yml`.
