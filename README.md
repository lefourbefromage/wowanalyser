# WoW Analyser

Application web locale qui compare la rotation DPS de deux joueurs de même spécialisation sur une clé Mythique+, à partir de deux liens Warcraft Logs. Claude en tire ensuite une analyse en français des erreurs de rotation, avec des conseils concrets.

## Ce que fait l'application

1. Tu colles deux liens du type `https://fr.warcraftlogs.com/reports/3tnfQcrCApPjFMZJ?fight=4`.
2. L'application charge les deux rapports. Elle présélectionne la clé du lien (`fight=N`, ou la dernière clé si `fight=last` ou si le paramètre est absent) et propose les joueurs qui ont la même classe et la même spé dans les deux clés.
3. Clique sur **Analyser**. Pour chaque joueur, elle récupère tous les events de la clé complète (trash et boss, avec la pagination), puis calcule :
   - le **DPS**, familiers inclus (d'après la table « damage-done » de WCL) et hors familiers ;
   - le **temps mort** : les trous entre deux sorts plus longs que le seuil (1,5 s par défaut, réglable), comptés uniquement en combat. Les trajets entre les packs et le temps passé mort sont exclus ;
   - la **fréquence de chaque sort** (casts par minute en combat) ;
   - l'**uptime des debuffs et DoTs** posés par le joueur, détectés automatiquement pour n'importe quelle spé ;
   - le **gaspillage de ressources** (`waste` des events `resourcechange`) et le % de sorts lancés avec la ressource au maximum ;
   - la répartition des dégâts, l'uptime des buffs personnels, l'ouverture et le rythme sur chaque boss, et les enchaînements de sorts les plus fréquents.
4. Le diff chiffré (pas les events bruts) est envoyé à Claude, qui rédige l'analyse en français.

## Installation

Prérequis : **Python 3.10 ou plus récent** ([python.org](https://www.python.org/downloads/) ; sous Windows, coche « Add python.exe to PATH » pendant l'installation).

```bash
git clone https://github.com/lefourbefromage/wowanalyser.git
cd wowanalyser
python -m venv .venv
```

Active l'environnement virtuel :

- Windows (PowerShell) : `.venv\Scripts\Activate.ps1`
- macOS / Linux : `source .venv/bin/activate`

Puis installe les dépendances :

```bash
pip install -r requirements.txt
```

## Configuration

Copie `.env.example` en `.env` et renseigne au moins la clé Warcraft Logs :

```
WCL_API_KEY=ta_cle_v1_warcraftlogs
ANTHROPIC_API_KEY=
ANTHROPIC_MODEL=claude-sonnet-5
```

- **WCL_API_KEY** : clé de l'API v1. Tu la trouves sur warcraftlogs.com, dans ton profil, section « Web API ».
- **ANTHROPIC_API_KEY** : facultative au démarrage. Tu peux la saisir directement dans l'application (bouton « Clé Anthropic »).

Le fichier `.env` est dans `.gitignore` : il n'est jamais commité.

### Obtenir une clé Anthropic

1. Va sur [console.anthropic.com](https://console.anthropic.com) et crée un compte.
2. Ouvre **Settings**, puis **API Keys**.
3. Clique sur **Create Key**, donne-lui un nom et copie-la.
4. Ajoute des crédits dans **Settings › Billing** si nécessaire : l'API n'est pas gratuite au-delà d'un petit crédit d'essai. Une analyse coûte généralement quelques centimes.
5. Colle la clé dans l'application (bouton « Clé Anthropic »). L'application vérifie la clé puis l'écrit dans le `.env` local. Elle n'est envoyée qu'à l'API Anthropic.

## Lancement

```bash
flask run
```

Ouvre ensuite <http://127.0.0.1:5000>. Le serveur n'écoute qu'en local.

## Structure

```
app.py                  routes Flask (API JSON + page)
backend/
  config.py             lecture / écriture du .env
  urls.py               extraction du code de rapport et du paramètre fight
  wcl.py                client API v1 Warcraft Logs (espacement, 429, pagination, cache)
  rotation.py           normalisation générique de la rotation d'un joueur
  compare.py            diff structuré entre les deux joueurs
  claude.py             appel à l'API Anthropic et gestion des erreurs
  jobs.py               analyses en arrière-plan avec progression
frontend/
  templates/index.html  page unique
  static/app.js         logique de l'interface (JS natif)
  static/style.css
```

## Limites connues

- **API Warcraft Logs v1** : environ 3600 points par heure. L'application espace ses appels, compte ses requêtes (compteur en haut à droite) et affiche « limite API atteinte, réessaie dans X minutes » en cas de dépassement. Une clé M+ d'environ 25 minutes demande en général 5 à 8 requêtes par joueur. Les résultats sont mis en cache 30 minutes.
- **Temps mort** : après un sort canalisé, un trou peut être normal. Le détail « sorts suivis d'un trou » aide à faire la différence, et Claude en tient compte.
- **Uptime des debuffs** : il est calculé sur la durée de vie observée de chaque cible, c'est-à-dire entre la première et la dernière interaction du joueur avec elle.
- Les noms de sorts sont en anglais (`translate=true`), pour rester identiques d'un rapport à l'autre.
