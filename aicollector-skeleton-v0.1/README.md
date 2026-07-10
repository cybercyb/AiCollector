# AICollector

[![Python](https://img.shields.io/badge/python-3.12%2B-blue.svg)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Status](https://img.shields.io/badge/status-production--ready-brightgreen.svg)](#)

**Serveur de collecte de connaissances en lecture seule — conçu pour les agents IA.**

> *Philosophie : AICollector ne juge jamais, ne modifie jamais, ne décide jamais.*
> *Il observe, normalise et rapporte.*

---

## Table des matières

1. [ Présentation ](#1-pr%C3%A9sentation)
2. [ Installation rapide (dev) ](#2-installation-rapide-dev)
3. [ Installation production ](#3-installation-production)
4. [ Utilisation ](#4-utilisation)
5. [ Architecture technique ](#5-architecture-technique)
6. [ Sécurité & Résilience ](#6-s%C3%A9curit%C3%A9--r%C3%A9silience)
7. [ Schémas de données (Pydantic V2) ](#7-sch%C3%A9mas-de-donn%C3%A9es-pydantic-v2)
8. [ Collecteurs disponibles ](#8-collecteurs-disponibles)
9. [ Ajouter un nouveau collecteur ](#9-ajouter-un-nouveau-collecteur)
10. [ API & Format JSON ](#10-api--format-json)
11. [ Journalisation (NDJSON) ](#11-journalisation-ndjson)
12. [ Autodiagnostic au démarrage ](#12-autodiagnostic-au-d%C3%A9marrage)
13. [ Tests & Validation ](#13-tests--validation)
14. [ Développement & Contribution ](#14-d%C3%A9veloppement--contribution)
15. [ Décisions d'architecture clés ](#15-d%C3%A9cisions-darchitecture-cl%C3%A9s)

---

## 1. Présentation

AICollector est un service système conçu pour fonctionner en tant que **serveur de connaissances en lecture seule**. Il collecte périodiquement (par défaut toutes les 2 heures via cron) l'état du serveur et le stocke sous forme de fichiers JSON versionnés. Il détecte les changements entre deux exécutions, maintient un historique de snapshots, et expose toutes les données sous forme de fichiers JSON structurés prêts à être consommés par un agent IA externe.

### Ce que fait AICollector

- **Observe** : collecteurs qui lisent le système de fichiers `/proc`, `/sys`, `psutil` — sans jamais rien modifier
- **Normalise** : schémas Pydantic V2 stricts, validation dynamique, sanitization dual-layer
- **Rapporte** : JSON versionnés + journal NDJSON structuré avec `run_id`, `timestamp_iso`, `level`

### Ce que AICollector NE fait PAS

- Aucune modification du système
- Aucun jugement (ne dit pas si quelque chose est "bon" ou "mauvais")
- Aucune décision automatique basée sur les données
- Aucune connexion réseau sortante (sauf configuration optionnelle)

---

## 2. Installation rapide (dev)

Aucun installateur requis — exécutez simplement le collecteur en mode dev :

```bash
git clone <repo>
cd aicollector

# Mode développement : redirection automatique des paths FHS
python -m aicollector --dev-mode

# Équivalent manuel
export AICOLLECTOR_ROOT=.
python -m aicollector
```

**Mapping automatique des chemins en mode dev :**

| Chemin production | Chemin dev |
|---|---|
| `/var/lib/aicollector/knowledge/` | `./data/knowledge/` |
| `/var/log/aicollector/` | `./logs/` |
| `/run/aicollector/aicollector.lock` | `./data/aicollector.lock` |

---

## 3. Installation production

### Prérequis

- Python ≥ 3.12
- Ubuntu 26.04 LTS (ou équivalent)
- Accès root (sudo) — requis pour certains collecteurs (`firewall`, `auditd`)

### Installation

```bash
# Cloner le dépôt
sudo git clone <repo> /opt/aicollector
cd /opt/aicollector

# Installer les dépendances
sudo ./scripts/install.sh

# Activer le service systemd
sudo systemctl enable aicollector
sudo systemctl start aicollector

# Vérifier le statut
sudo systemctl status aicollector
```

### Configuration

Le fichier de configuration se trouve dans **`/opt/aicollector/config.yaml`** :

```yaml
# AICollector — Fichier de configuration principal

# Intervalle de collecte (secondes)
collection_interval: 7200  # 2 heures

# Niveau de journalisation
log_level: INFO

# Liste des collecteurs actifs
collectors:
  cpu:
    enabled: true
    timeout: 20
  memory:
    enabled: true
    timeout: 20
  disk:
    enabled: true
    timeout: 30
  docker:
    enabled: false    # Dépend de la présence de Docker
  firewall:
    enabled: false    # Requiert sudo
    timeout: 30
  auditd:
    enabled: false    # Requiert sudo + auditd installé
    timeout: 30
```

---

## 4. Utilisation

### Exécution manuelle

```bash
# Mode production (root)
sudo python -m aicollector

# Mode dev (utilisateur)
python -m aicollector --dev-mode

# Mode verbose
python -m aicollector --dev-mode --verbose

# Vérifier la configuration
python -m aicollector --check-config
```

### Planification cron (par défaut)

```cron
# /etc/cron.d/aicollector
0 */2 * * * root /opt/aicollector/venv/bin/python -m aicollector >> /var/log/aicollector/cron.log 2>&1
```

### Chemins de données

```
/var/lib/aicollector/knowledge/
├── cpu/
│   ├── 2026-07-10T14-00-00Z.json    ← Snapshot horodaté
│   ├── 2026-07-10T12-00-00Z.json
│   └── changes.json                  ← Détection des changements
├── memory/
├── disk/
└── ...

/var/log/aicollector/
├── collector.log                     ← Fichier rotatif
└── collector.ndjson                  ← Journal structuré (NDJSON)

/run/aicollector/
└── aicollector.lock                  ← Fichier de lock (empêche double run)
```

---

## 5. Architecture technique

### Vue d'ensemble

```
aicollector/
├── core/
│   ├── __init__.py
│   ├── registry.py           # Registre dynamique des collecteurs
│   ├── schemas.py            # Schémas Pydantic V2 + registre de validation
│   ├── sanitizer.py          # Protection dual-layer (log + path)
│   ├── config_loader.py      # Chargement YAML avec validation
│   ├── self_diagnostic.py    # Pré-vol : vérifications environnementales
│   └── orchestrator.py       # Orchestration du cycle de collecte
├── collectors/
│   ├── base.py               # Classe de base BaseCollector
│   ├── cpu.py
│   ├── memory.py
│   ├── disk.py
│   ├── network.py
│   ├── docker.py
│   ├── systemd_services.py
│   ├── firewall.py
│   └── auditd.py
├── tests/
│   ├── unit/
│   ├── integration/
│   └── collectors/
├── scripts/
│   ├── install.sh
│   ├── uninstall.sh
│   └── check_dependencies.sh
├── config.yaml
├── requirements.txt
├── DECISIONS.md
├── SPECIFICATION.md
└── DEVELOPER_GUIDE.md
```

### Registre dynamique des collecteurs (`core/registry.py`)

Les collecteurs sont découverts dynamiquement via un **registre de classes** — pas de scan de répertoire, pas de plugin risky. Chaque collecteur s'enregistre lui-même :

```python
@register_collector(name="cpu")
class CPUCollector(BaseCollector):
    name: ClassVar[str] = "cpu"
    # ...
```

**Politique `sys.path` (Décision #11) :**

L'import dynamique utilise **`sys.path.append()`** — jamais `sys.path.insert(0, ...)`. Cela garantit :
- Pas de shadowing accidentel de modules stdlib
- Pas de comportement non déterministe entre les runs
- Résilience complète aux erreurs d'import (`try/except ImportError` + logging)

```python
# Correct : append, jamais insert(0, ...)
sys.path.append(str(collector_dir))
# Incorrect (interdit) : sys.path.insert(0, ...)
```

### Sanitization dual-layer (`core/sanitizer.py`)

**Couche 1 — Protection des logs :**
- Détection et masquage automatique des credentials (passwords, tokens, keys)
- Regex de pattern matching multi-formats
- Aucune donnée sensible dans les logs

**Couche 2 — Protection des paths :**
- Validation des chemins collectés (préfixes autorisés : `/proc/`, `/sys/`, `/dev/`, `/sys/class/net/`)
- Désinfection des `..` path traversal
- Seuil de profondeur maximale pour éviter la récursion infinie
- Vérification des symlinks (pas de liens vers l'extérieur)

```python
_ALLOWED_PROC_PREFIXES: frozenset[str] = frozenset({
    "/proc/", "/sys/", "/dev/", "/sys/class/net/",
})
_MAX_DEPTH: int = 15  # Profondeur max pour éviter récursion
```

### Validation par schémas Pydantic V2 (`core/schemas.py`)

Chaque collecteur dispose d'un schéma Pydantic strict avec :

- **Validation dynamique** via `_COLLECTOR_SCHEMA_REGISTRY` + `validate_knowledge_json()`
- **Type hints stricts** : `Literal["cpu"]`, `Annotated`, `Field`
- **Valeurs par défaut intelligentes** : `Field(default_factory=list)`
- **Métadonnées de version** : `schema_version`, `collector_version`
- **Compatibilité forward** : champs `None` plutôt que absents

```python
from typing import Annotated, Any, Literal, Union

@register_collector_schema(name="ram")
class RAMCollectorSchema(BaseModel):
    source: Literal["ram"]
    timestamp_utc: str
    schema_version: str = "1.0"
    # ...

def validate_knowledge_json(data: dict[str, Any]) -> BaseModel:
    source = data.get("source", "unknown")
    schema_cls = _COLLECTOR_SCHEMA_REGISTRY.get(source)
    if schema_cls is None:
        return data  # raw dict si inconnu
    return schema_cls.model_validate(data)
```

**Correction critique (Décision #17) :** Le type `Literal` doit être importé depuis `typing`. Sans cela → `NameError` au chargement du module.

---

## 6. Sécurité & Résilience

### Principes de sécurité

| Mécanisme | Protection |
|---|---|
| **Lecture seule** | Aucun write, aucun chmod, aucun chown |
| **Validation Pydantic** | Chaque JSON est validé contre son schéma avant écriture |
| **Sanitization logs** | Credentials masqués dans tous les logs NDJSON |
| **Sanitization paths** | Seuls les chemins autorisés sont collectés (`/proc/`, `/sys/`...) |
| **Pré-vol check** | `self_diagnostic.py` valide l'environnement avant chaque run |
| **Lock file** | Fichier `aicollector.lock` empêche le double-run |
| **Validation CLI** | Décision #14 : validation stricte des arguments de commande |

### Pré-vol (`self_diagnostic.py`)

Avant chaque exécution, AICollector vérifie :

1. **Python ≥ 3.12** — version minimale requise
2. **Plateforme Linux** — validation OS
3. **Répertoires accessibles** — `/var/lib/aicollector`, `/var/log/aicollector`
4. **Espace disque ≥ 100 MB** — test sur le **répertoire parent** (pas le chemin qui peut ne pas encore exister)
5. **Permissions d'écriture** — test réel par écriture temporaire

**Points clés de robustesse :**
- `shutil.disk_usage()` est appelé sur le **répertoire parent** (`base_dir.parent`), pas sur `base_dir` qui peut ne pas exister au premier démarrage
- Le répertoire parent existe toujours sur un système Linux fonctionnel
- Les fichiers de test temporaires sont écrits dans `/tmp/` avec cleanup `try/finally`
- En cas de `FileNotFoundError` → warning, jamais de plantage

### Protection contre l'injection NDJSON (Décision #15)

Chaque ligne NDJSON inclut automatiquement :
- `run_id` : UUID du run (injecté via `LoggerAdapter`)
- `timestamp_iso` : datetime ISO 8601 avec timezone UTC
- `level` : niveau du log (DEBUG, INFO, WARNING, ERROR)
- `module` : nom du module source
- **Pas de `\n` dans les valeurs** — échappement obligatoire

---

## 7. Schémas de données (Pydantic V2)

### Structure JSON générée

Chaque snapshot suit le format :

```json
{
  "source": "cpu",
  "timestamp_utc": "2026-07-10T14:00:00Z",
  "schema_version": "1.0",
  "collector_version": "1.0.0",
  "hostname": "my-server",
  "data": { ... }
}
```

### Changements (`changes.json`)

```json
{
  "source": "cpu",
  "previous_hash": "abc123...",
  "current_hash": "def456...",
  "changed": true,
  "timestamp_utc": "2026-07-10T14:00:00Z"
}
```

### Validation

```python
from core.schemas import validate_knowledge_json

data = validate_knowledge_json(raw_json_dict)
# → Retourne un modèle Pydantic validé ou le dict brut si inconnu
```

---

## 8. Collecteurs disponibles

| Collecteur | Description | Requis root | Timeout |
|---|---|---|---|
| `cpu` | Infos CPU, load, fréquences | Non | 20s |
| `memory` | RAM, swap, zones mémoire | Non | 20s |
| `disk` | Utilisation partitions | Non | 30s |
| `network` | Interfaces, stats réseau | Non | 20s |
| `docker` | Conteneurs et images | Non | 30s |
| `systemd_services` | Services systemd | Non | 20s |
| `firewall` | Règles UFW / iptables | **Oui** | 30s |
| `auditd` | Journaux auditd | **Oui** | 30s |

### Structure d'un collecteur

```python
from collectors.base import BaseCollector

class MonCollecteur(BaseCollector):
    """Description courte du collecteur."""

    name: ClassVar[str] = "mon_collecteur"
    schema_version: ClassVar[str] = "1.0"
    collector_version: ClassVar[str] = "1.0.0"
    requires_root: ClassVar[bool] = False
    timeout_seconds: ClassVar[int] = 20

    def collect(self, adapter: SystemAdapter) -> dict:
        """Collecte les données et retourne un dict."""
        ...
```

---

## 9. Ajouter un nouveau collecteur

### Étapes de développement

**1. Créer le fichier** `collectors/mon_collecteur.py`

**2. S'enregistrer dans `core/registry.py` :**
```python
from core.registry import register_collector

@register_collector(name="mon_collecteur")
class MonCollecteur(BaseCollector):
    ...
```

**3. Créer le schéma Pydantic dans `core/schemas.py` :**
```python
@register_collector_schema(name="mon_collecteur")
class MonCollecteurSchema(BaseModel):
    source: Literal["mon_collecteur"]
    timestamp_utc: str
    schema_version: str = "1.0"
    collector_version: str = "1.0.0"
    hostname: str
    data: dict[str, Any]
    extra_topics: list[str] = Field(default_factory=list)
```

**4. Activer dans `config.yaml` :**
```yaml
collectors:
  mon_collecteur:
    enabled: true
    timeout: 30
```

**5. Tester :**
```bash
python -m aicollector --dev-mode
# Vérifier data/mon_collecteur/YYYY-MM-DDTHH-MM-SSZ.json
```

### Collecteurs conditionnels (avec dépendances)

Pour les collecteurs nécessitant un package externe (`auditd`, `firewall`) :

1. Ajouter le bloc conditionnel dans `scripts/install.sh`
2. Ajouter le bloc dans `scripts/uninstall.sh`
3. Ajouter le check dans `scripts/check_dependencies.sh`
4. Documenter dans `DEPENDENCIES.md`

---

## 10. API & Format JSON

### Chemins de données (FHS Linux)

```
/var/lib/aicollector/knowledge/{collector}/YYYY-MM-DDTHH-MM-SSZ.json
/var/lib/aicollector/knowledge/{collector}/changes.json

/run/aicollector/aicollector.lock

/var/log/aicollector/collector.log       (rotatif)
/var/log/aicollector/collector.ndjson    (structuré)
```

### Détection de changements (SHA256)

- Chaque snapshot est hashe en SHA256
- Le fichier `changes.json` compare le hash courant avec le hash précédent
- Si différent → nouveau snapshot créé
- Les snapshots historiques sont conservés

### Mode dev

```bash
export AICOLLECTOR_ROOT=.
# Génère dans ./data/knowledge/{collector}/
```

---

## 11. Journalisation (NDJSON)

AICollector utilise un **journal NDJSON structuré** pour une intégrabilité maximale avec les outils SIEM et les agents IA.

### Format d'une ligne

```json
{"run_id":"a1b2c3d4-...","timestamp_iso":"2026-07-10T14:00:00+00:00","level":"INFO","module":"core.orchestrator","message":"Collecte terminée","collector":"cpu","duration_ms":142}
```

### Rotation des logs

- Fichier `.log` : rotation standard via `RotatingFileHandler` (max 10 MB, 5 backups)
- Fichier `.ndjson` : append-only, aucune rotation (consommé en temps réel)

### Credentials masqués

Tous les patterns de credentials sont détectés et remplacés avant écriture :
- `password=*` → `password=***`
- `token=*` → `token=***`
- `api_key=*` → `api_key=***`
- Clés JSON sensibles

---

## 12. Autodiagnostic au démarrage

Le module `self_diagnostic.py` est exécuté automatiquement au démarrage de chaque run.

### Ce qu'il vérifie

```
[CHECK] Python version      : >= 3.12         ✅
[CHECK] Plateforme          : Linux           ✅
[CHECK] Répertoire base     : /var/lib/...    ✅
[CHECK] Espace disque       : 512 MB          ✅
[CHECK] Permissions write   : /var/log/...    ✅
[WARN ] Répertoire base     : n'existe pas encore (sera créé)
```

### Points de robustesse

- **Espace disque** : testé sur le **répertoire parent** (`Path.parent`), pas sur le répertoire cible
- **Permissions** : écriture réelle d'un fichier temporaire, puis cleanup via `finally`
- **`FileNotFoundError`** → logged comme warning, ne bloque pas le run
- En cas d'erreur fatale → levée de `AICollectorError` avec message explicite

---

## 13. Tests & Validation

### Exécution des tests

```bash
# Tous les tests unitaires
pytest tests/unit/ -v

# Tests d'un module spécifique
pytest tests/unit/test_config_loader.py -v

# Couverture des tests
pytest tests/unit/ --cov=aicollector --cov-report=html

# Tests d'intégration
pytest tests/integration/ -v

# Tests des collecteurs
pytest tests/collectors/test_cpu.py -v
```

### Validation de code

```bash
# Vérification syntaxe Python
python -m py_compile core/*.py collectors/*.py

# Vérification des schémas Pydantic
python -c "from core.schemas import _COLLECTOR_SCHEMA_REGISTRY; print(_COLLECTOR_SCHEMA_REGISTRY.keys())"

# Vérification des dépendances
sudo ./scripts/check_dependencies.sh
```

### Validation des JSON générés

```bash
# Vérifier la validité du JSON et la conformité au schéma
python -c "
from core.schemas import validate_knowledge_json
import json, sys
with open(sys.argv[1]) as f:
    data = json.load(f)
result = validate_knowledge_json(data)
print('✅ JSON valide et conforme au schéma:', result.source)
" /var/lib/aicollector/knowledge/cpu/2026-07-10T14-00-00Z.json
```

---

## 14. Développement & Contribution

### Workflow standard

```
1. Modifier le code d'un collecteur ou du core
2. Exécuter les tests : pytest tests/unit/ -v
3. Tester manuellement : python -m aicollector --dev-mode
4. Vérifier les JSON générés
5. Commit et push
```

### Bonnes pratiques

**Nommage :**
- Fichiers JSON : `snake_case` pour les clés
- Répertoires : `kebab-case`

**Typage :**
- Utiliser `typing` (Python 3.12+)
- Pas de `from __future__ import annotations` dans les modules critiques (performance)

**Gestion des erreurs :**
- Ne jamais avaler une exception silencieusement
- Logger les erreurs avant de les propager
- `AICollectorError` pour les erreurs métier

**Documentation :**
- Mettre à jour `DECISIONS.md` pour toute décision technique
- Mettre à jour `SPECIFICATION.md` si nécessaire
- Documenter les schémas dans `core/schemas.py`

---

## 15. Décisions d'architecture clés

> Version consolidée au 2026-07-10 · 18 décisions

| # | Date | Sujet | Statut |
|---|---|---|---|
| 1 | 2026-07-06 | Langage de collecte | RENVERSÉE |
| 2 | 2026-07-07 | Mécanisme d'exécution des collecteurs | **ACTIVE** |
| 3 | 2026-07-07 | Format de stockage des snapshots | **ACTIVE** |
| 4 | 2026-07-07 | Mécanisme de détection de changements | RENVERSÉE |
| 5 | 2026-07-07 | Format des changements | **ACTIVE** |
| 6 | 2026-07-08 | Journalisation | **ACTIVE** |
| 7 | 2026-07-08 | Format du journal de logs | **ACTIVE** |
| 8 | 2026-07-08 | Validation Pydantic | **ACTIVE** |
| 9 | 2026-07-08 | Registre des collecteurs | **ACTIVE** |
| 10 | 2026-07-08 | Gestion conditionnelle des collecteurs | **ACTIVE** |
| 11 | 2026-07-08 | Politique sys.path (append, pas insert) | **ACTIVE** |
| 12 | 2026-07-09 | Validation CLI (arguments de commande) | **ACTIVE** |
| 13 | 2026-07-09 | Sanitization dual-layer (log + path) | **ACTIVE** |
| 14 | 2026-07-09 | Préfixes autorisés pour /proc | **ACTIVE** |
| 15 | 2026-07-09 | Protection injection NDJSON | **ACTIVE** |
| 16 | 2026-07-09 | Fichier de lock (os.kill avec signal 0) | **ACTIVE** |
| 17 | 2026-07-10 | Correction NameError Literal + RAM schema | **ACTIVE** |
| 18 | 2026-07-10 | Robustesse self_diagnostic (disk check parent) | **ACTIVE** |

### Décision #11 — Politique `sys.path`

```python
# ✅ CORRECT : append, jamais insert(0, ...)
sys.path.append(str(collector_dir))

# ❌ INTERDIT : insert(0, ...) shadow les modules stdlib
sys.path.insert(0, str(collector_dir))
```

### Décision #13 — Sanitization dual-layer

```
INPUT → [Log Sanitizer] → [Path Sanitizer] → OUTPUT
           ↓                    ↓
        Credentials         Allowed prefixes
        masqués (/proc/, /sys/,
                 /dev/, /sys/class/net/)
```

### Décision #17 — Schémas Pydantic

```python
# ✅ Obligatoire : importer Literal depuis typing
from typing import Annotated, Any, Literal, Union

# ✅ Option : champs optionnels avec valeur None
capabilities: CPUCapabilities | None = None

# ❌ ERREUR : source="ram" conflict si RAMCollectorSchema utilise Literal["ram"]
#  → Résoudre en renommant le champ ou en utilisant un schéma générique
```

### Décision #18 — Pré-vol disk check

```python
# ✅ CORRECT : répertoire parent existe toujours
stat = shutil.disk_usage(base_dir.parent)

# ❌ INCORRECT : base_dir peut ne pas exister au premier démarrage
stat = shutil.disk_usage(base_dir)  # → FileNotFoundError
```

---

## Licence

MIT · Voir [LICENSE](LICENSE)
