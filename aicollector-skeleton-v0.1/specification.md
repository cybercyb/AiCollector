[SPECIFICATION V1.2.md](https://github.com/user-attachments/files/30080781/SPECIFICATION.V1.2.md)
> **STATUT : VERSION FIGÉE ET VALIDÉE — Référence technique absolue du projet. Toute modification future doit d'abord être répercutée ici avant toute modification du code.**  
> **Date de verrouillage :** 5 juillet 2026 — Version verrouillée par le client après revue complète.

# AICollector — Spécification Technique Complète

**Version du document :** 1.3  
**Date :** 2026-07-14  
**cible :** Python ≥ 3.12 · Ubuntu 26.04 LTS  

---

> **Statut :** Ce document est la **référence technique absolue** du projet AICollector. Aucun développement ne doit commencer tant qu'il n'a pas été validé. Toute modification du code en cours d'implémentation doit être reportée dans ce document dans le même commit.

---

## Table des matières

1. [Présentation du projet](#1-présentation-du-projet)
2. [Architecture globale](#2-architecture-globale)
3. [Description détaillée des modules](#3-description-détaillée-des-modules)
4. [Schéma des fichiers JSON](#4-schéma-des-fichiers-json)
5. [Format de `manifest.json`](#5-format-de-manifestjson)
6. [Format de `changes.json`](#6-format-de-changesjson)
7. [Sécurité](#7-sécurité)
8. [Performances](#8-performances)
9. [Gestion des erreurs](#9-gestion-des-erreurs)
10. [Extensibilité](#10-extensibilité)
11. [Standards de développement](#11-standards-de-développement)
12. [Roadmap](#12-roadmap)
13. [Analyse critique](#13-analyse-critique)

---

## 1. Présentation du projet

### 1.1 Objectifs

AICollector est un **collecteur de connaissances en lecture seule** pour serveur Linux Ubuntu, dont la mission unique est de fournir à une IA tierce (agent LLM externe) une **représentation fiable, factuelle et versionnée de l'état d'un serveur**. Il ne prend jamais de décision, neconseille jamais, ne modifie jamais le système. Il observe, normalise, compare et persist.

L'objectif fonctionnel est triple :

- **Collecter** exhaustivement les informations système pertinentes via des collecteurs spécialisés (CPU, RAM, réseau, stockage, Docker, services systemd, pare-feu, etc.) ;
- **Normaliser** chaque donnée collectée en JSON structuré, typé et versionné, avec un identifiant unique du serveur (`server_uuid`) et un horodatage canonique (`timestamp_utc`) ;
- **Détecter les changements** entre deux exécutions via une comparaison SHA256, et maintenir une base de connaissances versionnée permettant à un agent IA de comprendre l'évolution du système.

### 1.2 Philosophie

Le projet obéit à trois principes non négociables :

| Principe | Signification concrète |
|---|---|
| **Lecture seule stricte** | Aucune commande destructive n'est exécutée, aucun fichier n'est modifié, aucun service n'est redémarré. L'outil ne fait qu'observer. |
| **Zéro jugement** | AICollector ne dit jamais "c'est bien ou mal". Il détecte un changement et le signale avec sa sévérité factuelle. L'interprétation appartiennent à l'IA consommatrice. |
| **Extensibilité par conception** | Ajouter un nouveau collecteur ne nécessite jamais de modifier le cœur du pipeline. L'architecture repose sur un registre dynamique et un décorateur d'enregistrement. |

### 1.3 Périmètre

**Dans le périmètre (V1.0) :**

- Collecte de 15 catégories d'informations système sur Ubuntu 26.04 LTS
- Auto-découverte dynamique des collecteurs via `pkgutil.iter_modules()`
- Pipeline en 4 phases : COLLECT → NORMALIZE → COMPARE → KNOWLEDGE BASE
- Détection de changements par hash SHA256
- Base de connaissances versionnée avec historique configurable (par nombre de versions)
- Système d'événements (EventBus) synchrone in-process
- Couche d'abstraction système (SystemAdapter) avec whitelist de commandes
- Exécution pilotée par cron externe + fichier de verrou
- Sécurité en défense en profondeur : whitelist de commandes, sanitizer, exclusions de secrets

**Hors périmètre (V1.0, roadmap future) :**

- Mode daemon / service interne
- Interface web, API REST, CLI interactive
- Notifications push (email, Slack, webhook)
- Intégration LLM native (déclenchement automatique d'analyse IA)
- Collecte sur hôtes distants (SSH)
- Base de données relationnelle (SQLite/PostgreSQL)
- Chiffrement de la base de connaissances
- Agent root dédié avec élévation sudo contrôlée

### 1.4 Contraintes

- **Plateforme cible :** Ubuntu 26.04 LTS (Server) — les collecteurs utilisent des chemins Linux standards (`/proc`, `/sys`, `/var/lib/docker`, `/etc/systemd`, etc.)
- **Python :** ≥ 3.12 (requis pour les dataclasses `slots=True`, `frozen`, la syntaxe moderne de type hinting)
- **Dépendances externes :** `pydantic` (≥ 2.x) et `pyyaml` sont des **dépendances obligatoires**. Aucune autre dépendance Python externe n'est requise. Les dataclasses de transit (`CollectorResult`, `PipelineStats`, `Event`) restent en stdlib. Elles sont installées via `requirements.txt` / `pyproject.toml`.
- **Permissions :** Lecture seule. Certains collecteurs nécessitent les droits root (accès `/proc/sys/kernel/ns_last_pid`, `/sys/block/*/device/smartctl/attributes`, etc.) — le comportement avec/sans root doit être géré proprement.
- **Pas d'activité hors exécution :** Entre deux runs cron, le processus ne consume aucune ressource.

### 1.5 Fonctionnement général

Le cycle d'exécution complet est le suivant :

```
┌─────────────────────────────────────────────────────────────────┐
│                     TRIGGER (cron externe)                       │
│                  Fichier de verrou (lockfile)                    │
└────────────────────────────┬────────────────────────────────────┘
                             │ (empêche les runs simultanés)
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  PHASE 1 — COLLECT                                                 │
│  • Auto-découverte des collecteurs via pkgutil.iter_modules()    │
│  • Enregistrement dynamique dans le Registry                    │
│  • Pour chaque collecteur :                                       │
│      system_adapter.run_command() avec whitelist                │
│      → renvoie CollectorResult (données brutes, errors, perf)   │
│  • Événements émis sur l'EventBus                                │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  PHASE 2 — NORMALIZE                                              │
│  • Validation des données brutes via Pydantic (schema_version)   │
│  • Calcul du SHA256 sur JSON canonique                            │
│  • Ajout des métadonnées : schema_version, server_uuid,          │
│    collector_version, timestamp_utc, hash, source                 │
│  • Production du JSON final par collecteur                        │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  PHASE 3 — COMPARE                                                │
│  • Lecture du manifest.json de knowledge/                        │
│  • Comparaison SHA256 : identical / added / removed / modified   │
│  • Classification de sévérité (factuelle, jamais prescriptive)   │
│  • Émission des événements change.detected                       │
│  • Écriture des fichiers dans changes/                           │
└────────────────────────────┬────────────────────────────────────┘
                             │
                             ▼
┌─────────────────────────────────────────────────────────────────┐
│  PHASE 4 — KNOWLEDGE BASE                                         │
│  • Écriture / mise à jour des JSON dans knowledge/               │
│  • Mise à jour du manifest.json de knowledge/                    │
│  • Rotation de l'historique (history/) selon rétention config     │
│  • Purge FIFO des changes/ selon rétention configurée            │
└────────────────────────────┬─────────────────────────────────────┘
                             │
                             ▼
                          FIN
```

---

## 2. Architecture globale

### 2.1 Organisation des dossiers

```
/opt/aicollector/                   # Code de l'application — versionnable
│
├── collector.py                    # Point d'entrée unique (CLI)
├── VERSION                        # Version sémantique du projet (ex: 1.0.0)
│
├── core/                          # Cœur du pipeline — NE PAS modifier par plugin
│   ├── __init__.py
│   ├── base_collector.py          # ABC BaseCollector + dataclasses de transit
│   ├── system_adapter.py          # Couche d'abstraction — TOUS les appels système
│   ├── event_bus.py               # Bus d'événements synchrone in-process (pub/sub)
│   ├── registry.py                # Registre dynamique des collecteurs découverts
│   ├── pipeline.py                # Orchestrateur des 4 phases
│   ├── config_loader.py           # Chargement + validation Pydantic de config.yaml
│   ├── schemas.py                 # Schémas Pydantic pour les JSON de collecteur
│   ├── logger.py                  # Logger structuré NDJSON → /var/log/aicollector/
│   ├── lockfile.py                # Gestion du fichier de verrou
│   ├── hashing.py                 # Fonctions utilitaires SHA256
│   ├── diff_engine.py             # Moteur de comparaison SHA256 récursif
│   ├── sanitizer.py               # Nettoyage anti-fuite de secrets
│   ├── knowledge_store.py         # Lecture/écriture de /var/lib/aicollector/
│   ├── self_diagnostic.py         # Auto-diagnostic au démarrage
│   └── exceptions.py              # Hiérarchie d'exceptions custom
│
├── collectors/                    # Package de collecteurs (plugins auto-découverts)
│   ├── __init__.py               # Auto-discovery via pkgutil.iter_modules()
│   ├── base.py                   # Import commun (ré-export de BaseCollector)
│   ├── system.py                 # CollecteurHostname + OS + Kernel
│   ├── cpu.py                    # CollecteurCPU
│   ├── ram.py                    # CollecteurRAM
│   ├── storage.py                # CollecteurStorage (df + lsblk)
│   ├── smart.py                  # CollecteurSMART (smartctl)
│   ├── network.py                # CollecteurNetwork (ip, ss, interfaces)
│   ├── docker.py                 # CollecteurDocker
│   ├── systemd_services.py       # CollecteurSystemdServices
│   ├── firewall.py               # CollecteurFirewall (ufw + iptables)
│   ├── auditd.py                 # CollecteurAuditd
│   ├── apt.py                    # CollecteurAPT
│   ├── users.py                  # CollecteurUsers
│   ├── cron.py                   # CollecteurCron
│   ├── timers.py                 # CollecteurTimers
│   ├── ssl_certificates.py       # CollecteurSSLCertificates
│   └── syslogs.py                # CollecteurSyslogs
│
├── install.sh                     # Script d'installation idempotent (root)
└── README.md                      # Documentation utilisateur

/etc/aicollector/                  # Configuration — fichier unique
└── config.yaml                    # Configuration utilisateur (YAML)

/var/lib/aicollector/              # Données persistantes CRITIQUES
│
├── manifest.json                  # Index global (runs, global_hash, server_uuid)
├── knowledge/                     # Dernier snapshot de chaque collecteur
│   ├── system.json
│   ├── cpu.json
│   ├── ram.json
│   ├── storage.json
│   ├── smart.json
│   ├── network.json
│   ├── docker.json
│   ├── systemd_services.json
│   ├── firewall.json
│   ├── auditd.json
│   ├── apt.json
│   ├── users.json
│   ├── cron.json
│   ├── timers.json
│   ├── ssl_certificates.json
│   └── syslogs.json
│
├── history/                       # Historique versionné par collecteur
│   ├── system/
│   │   ├── 0001.json
│   │   ├── 0002.json
│   │   └── ...
│   ├── cpu/
│   ├── ram/
│   └── ... (un sous-répertoire par collecteur)
│
└── changes/                       # Détection de changements
    ├── manifest.json              # Index (total, par sévérité)
    ├── latest.json               # Dernier changement en date
    └── <timestamp>.json          # Un fichier par changement détecté

/var/cache/aicollector/            # Données régénérables, non critiques
└── cache/                         # Cache intra-run (SystemAdapter, tmp)

/run/aicollector/                  # Fichier de verrou volatile (tmpfiles.d)
└── aicollector.lock               # PID + timestamp + run_id

/var/log/aicollector/              # Logs — rotation via logrotate
└── aicollector.log                # Logs tournants (aicollector.log.1.gz, etc.)
```

### 2.2 Mode développement (`--dev-mode` / `AICOLLECTOR_ROOT`)

Pour développer, tester et débugger sans droits root ni installation système, le collecteur peut être exécuté en **mode développement**. Dans ce mode, tous les chemins FHS sont redirigés vers un répertoire de travail local relatif à l'emplacement du projet :

| Variable / Flag | Effet |
|---|---|
| `AICOLLECTOR_ROOT=/chemin/absolu` | Redirige TOUS les chemins (data, config, logs) vers ce préfixe |
| `AICOLLECTOR_ROOT=.` | Mode local par rapport au répertoire courant (équivalent `./data/`) |
| `python collector.py --dev-mode` | Flag CLI — définit `AICOLLECTOR_ROOT` vers `./dev_data/` automatiquement |
| `python collector.py --dev-mode --data-dir /tmp/aicollector-test` | Combinable pour un répertoire temporaire |

**Comportement en mode dev :**

| Chemin système (prod) | Chemin dev équivalent |
|---|---|
| `/opt/aicollector/` | Répertoire contenant `collector.py` (détection automatique) |
| `/etc/aicollector/config.yaml` | `./config.yaml` (dans le répertoire de travail) |
| `/var/lib/aicollector/knowledge/` | `./data/knowledge/` |
| `/var/lib/aicollector/history/` | `./data/history/` |
| `/var/lib/aicollector/changes/` | `./data/changes/` |
| `/var/cache/aicollector/cache/` | `./data/cache/` |
| `/var/log/aicollector/` | `./logs/` |
| `/run/aicollector/aicollector.lock` | `./data/aicollector.lock` |

**Détection automatique :** Si `AICOLLECTOR_ROOT` n'est pas défini et que `/opt/aicollector/` n'existe pas, le mode dev est activé par défaut avec `AICOLLECTOR_ROOT=.`.

**Validation :** `python collector.py --check` valide les chemins résolus et affiche clairement le mode actif (`MODE: production` ou `MODE: development`).

### 2.3 Rôle de chaque fichier

| Fichier / Répertoire | Rôle |
|---|---|
| `/opt/aicollector/collector.py` | Point d'entrée unique. Parse les arguments CLI (`--run`, `--config`, `--check`, `--check-lock`, `--version`, `--dev-mode`, `--dry-run`), charge la config, acquiert le lockfile, lance le pipeline. |
| `/opt/aicollector/core/system_adapter.py` | **Seule** interface entre le code et l'OS. Méthodes : `run_command()`, `read_proc_file()`, `read_sys_file()`, `list_directory()`. Tous les collecteurs passent par lui — jamais d'appel subprocess direct. |
| `/opt/aicollector/core/event_bus.py` | Bus d'événements synchrone in-process. Les subscribers s'enregistrent par type d'événement. Découple les collecteurs, le comparateur, et les futurs "réactions" (ex: alerte IA sur changement critique). Désactivable via config. |
| `/opt/aicollector/core/registry.py` | Registre singleton des collecteurs découverts. Méthodes : `register()`, `get_collector()`, `list_collectors()`. |
| `/opt/aicollector/core/pipeline.py` | Orchestrateur. Coordonne les 4 phases dans l'ordre. Gère l'arrêt propre en cas d'erreur d'un collecteur. |
| `/opt/aicollector/core/config_loader.py` | Charge `/etc/aicollector/config.yaml`, valide avec Pydantic, résout les chemins selon le mode (dev/prod) via `AICOLLECTOR_ROOT`. |
| `/opt/aicollector/core/lockfile.py` | Gère `/run/aicollector/aicollector.lock` (verrou anti-concurrent). Vérifie les PID morts. |
| `/opt/aicollector/core/knowledge_store.py` | Lecture / écriture de `/var/lib/aicollector/knowledge/` et `/var/lib/aicollector/history/`. Gère la rotation FIFO. |
| `/etc/aicollector/config.yaml` | Fichier de configuration utilisateur (YAML). Définit les collecteurs actifs, les seuils, les rétentions, les chemins personnalisés (optionnels). |
| `/var/lib/aicollector/knowledge/` | Base de connaissances persistée. Chaque fichier JSON = dernier snapshot d'un collecteur. |
| `/var/lib/aicollector/changes/` | Détections de changement. Un fichier par événement, manifest indexé. |
| `/var/lib/aicollector/history/` | Historique versionné. Sous-dossiers par collecteur, purge FIFO selon `config.retention.history_versions`. |
| `/var/lib/aicollector/manifest.json` | Manifeste de la knowledge base. Indexe tous les fichiers de `knowledge/` avec SHA256, timestamps, versions de schéma. |
| `/var/cache/aicollector/cache/` | Cache régénérable intra-run. Supprimable sans perte de données. |
| `/run/aicollector/aicollector.lock` | Fichier de verrou volatile. Recréé après reboot via `/etc/tmpfiles.d/aicollector.conf`. |
| `/var/log/aicollector/aicollector.log` | Journalisation structurée NDJSON. Rotation quotidienne via logrotate. |
| `/opt/aicollector/install.sh` | Script d'installation idempotent. Crée l'utilisateur, l'arborescence, les permissions, les fichiers systemd. |

### 2.4 Flux de fonctionnement (vue d'ensemble)

```
cron (toutes les 2h par défaut)
    │
    ▼
collector.py --run
    │
    ├── /run/aicollector/aicollector.lock  ← vérifie / acquiert le verrou
    │
    ├── config_loader.py
    │     └── Lecture de /etc/aicollector/config.yaml
    │         └── Résolution des chemins via AICOLLECTOR_ROOT
    │
    ├── self_diagnostic.py       ← Validation : Python ≥ 3.12,
    │                              chemins accessibles, outils système
    │
    ├── registry.py
    │     └── Auto-découverte des collecteurs dans /opt/aicollector/collectors/
    │
    ├── Phase 1 — COLLECT
    │     └── system_adapter.run_command() (whitelistée)
    │         └── CollectorResult (données brutes, erreurs, perf)
    │
    ├── Phase 2 — NORMALIZE
    │     └── Validation Pydantic + SHA256
    │         └── JSON par collecteur
    │
    ├── Phase 3 — COMPARE
    │     └── Lecture du manifest.json de /var/lib/aicollector/
    │         └── Diff SHA256 : identical / added / removed / modified
    │             └── Écriture dans /var/lib/aicollector/changes/
    │
    ├── Phase 4 — KNOWLEDGE BASE
    │     └── Écriture dans /var/lib/aicollector/knowledge/
    │         └── Mise à jour du manifest.json
    │             └── Rotation FIFO de /var/lib/aicollector/history/
    │
    └── /var/log/aicollector/aicollector.log  ← logs structurés NDJSON
```

## 2.2 Rôle de chaque fichier

| Fichier / Répertoire | Rôle |
|---|---|
| `collector.py` | Point d'entrée unique. Parse les arguments CLI ( `--run`, `--config`, `--check-lock`, `--version` ), charge la config, acquiert le lockfile, lance le pipeline. |
| `config.yaml` | Fichier de configuration utilisateur (YAML). Définit les seuils, les collecteurs actifs/inactifs, les options de rétention, les chemins personnalisés. |
| `VERSION` | Fichier texte plat contenant la version sémantique (ex: `1.0.0`). Lu par `collector.py` et injecté dans les métadonnées. |
| `manifest.json` (racine) | Manifeste global au niveau du projet. Indexe les runs : dernier run, nombre de runs totaux, global_hash du dernier run, état du système. |
| `core/` | Le cœur immutable du projet. Aucun collecteur ne doit en modifier le contenu. |
| `core/base_collector.py` | Définit l'interface `BaseCollector` (ABC) et les dataclasses de transit (`CollectorResult`, `CollectorCapabilities`, `PipelineStats`). |
| `core/system_adapter.py` | **Seule** interface entre le code et le système d'exploitation. Méthodes : `run_command()`, `read_proc_file()`, `read_sys_file()`, `list_directory()`. |
| `core/event_bus.py` | Bus d'événements synchrone in-process. Les subscribers (logger, etc.) s'enregistrent par type d'événement. |
| `core/registry.py` | Registre singleton des collecteurs découverts. Méthodes : `register()`, `get_collector()`, `list_collectors()`. |
| `core/pipeline.py` | Orchestrateur. Coordonne les 4 phases dans l'ordre. Gère l'arrêt propre en cas d'erreur. |
| `core/config_loader.py` | Charge `config.yaml`, valide avec Pydantic, expose un objet config typé. |
| `core/schemas.py` | Définit les modèles Pydantic (RootModel / BaseModel) pour les JSON produits par les collecteurs. |
| `core/logger.py` | Configure le logger Python (niveau, format JSON, rotation). S'abonne à l'EventBus pour journaliser tous les événements. |
| `core/lockfile.py` | Créé / vérifie / supprime le fichier de verrou. Empêche les runs simultanés. |
| `core/hashing.py` | Fonctions utilitaires : `compute_json_hash()`, `compute_file_hash()`. |
| `core/diff_engine.py` | Compare deux dictionnaires de données et renvoie la liste des changements (added/removed/modified) avec leurs chemins JSONPath. |
| `core/sanitizer.py` | Parcourt récursivement les données pour remplacer les secrets par `"***REDACTED***"`. |
| `core/knowledge_store.py` | Lecture / écriture de `knowledge/` et `history/`. Gère la rotation de l'historique et la purge FIFO. |
| `core/self_diagnostic.py` | Vérifie au démarrage : Python version, chemins accessibles, permissions, présence des outils système (docker, systemctl, etc.). |
| `core/exceptions.py` | Hiérarchie d'exceptions : `AICollectorError`, `CollectorError`, `ForbiddenCommandError`, `LockfileError`, `SchemaValidationError`, etc. |
| `collectors/` | Package de plugins. Chaque module expose un collecteur auto-enregistré via `@register_collector("nom")`. |
| `knowledge/` | Persistance de la base de connaissances. Chaque fichier JSON = dernière快照 d'un collecteur. |
| `changes/` | Persistance des détections de changement. Un fichier par changement, manifest indexé. |
| `cache/` | Répertoire technique. Utilisé par `SystemAdapter` pour des fichiers temporaires intra-run si nécessaire. |
| `history/` | Historique versionné. Sous-dossiers par collecteur, purge FIFO selon `config.history.retention_versions`. |
| `logs/` | Fichiers de log tournants (rotation quotidienne, retention configurable). |

### 2.3 Flux de fonctionnement (vue d'ensemble)

```
collector.py (entry point)
    │
    ├── config_loader.py → Charge et valide config.yaml
    ├── lockfile.py      → Acquiert le verrou (quitearly si déjà locké)
    └── pipeline.py      → Exécute les 4 phases
              │
              ├─ Phase COLLECT
              │     registry.py (auto-discover) → charge tous les collecteurs
              │     Pour chaque collecteur :
              │         system_adapter.py (appel système whitelisté)
              │         → dataclasses CollectorResult
              │         EventBus.emit("collector.finished")
              │
              ├─ Phase NORMALIZE
              │     schemas.py (validation Pydantic)
              │     hashing.py (SHA256)
              │     sanitizer.py (nettoyage secrets)
              │
              ├─ Phase COMPARE
              │     diff_engine.py (comparaison SHA256)
              │     knowledge_store.py (lecture ancien hash)
              │     → Émet events change.detected
              │     → Écrit changes/<timestamp>.json
              │
              └─ Phase KNOWLEDGE BASE
                    knowledge_store.py (écriture JSON)
                    → Mise à jour knowledge/*.json
                    → Rotation history/ (FIFO)
                    → Purge changes/ (FIFO)
                    EventBus.emit("run.finished")
```

---

## 3. Description détaillée des modules

### 3.1 `core/__init__.py`

**Rôle :** Point d'entrée du package `core`. Ré-exporte les symboles publics de chaque module pour faciliter les imports dans le reste du projet (`from core import Pipeline, Registry, EventBus`).

**Dépendances :** Tous les modules de `core/` (imports circulaires évités par un import différé si nécessaire).

**Erreurs possibles :** Aucune (fichier d'init simple).

**Performances attendues :** Négligeable (< 1 ms).

---

### 3.2 `core/base_collector.py`

**Rôle :** Définit l'interface abstraite que tout collecteur doit implémenter, et les dataclasses de transit utilisées dans le pipeline.

**Entrées :** Aucune (classe de définition pure).

**Sorties :** Classes abstraites et dataclasses prêtes à l'emploi.

**Dépendances :** `abc`, `dataclasses`, `typing`.

**Classes définies :**

```python
class BaseCollector(ABC):
    name: ClassVar[str]              # Identifiant unique (ex: "cpu")
    schema_version: ClassVar[str]    # Version du schéma Pydantic (ex: "1.0")
    collector_version: ClassVar[str] # Version du collecteur (ex: "1.2.0")
    requires_root: ClassVar[bool]     # Nécessite les droits root
    timeout_seconds: ClassVar[int]    # Timeout de collect (défaut: 60)

    @abstractmethod
    def collect(self, system: SystemAdapter) -> CollectorResult:
        """Collecte les données brutes via le SystemAdapter."""
        ...

    def capabilities(self) -> CollectorCapabilities:
        """Renvoie les capacités du collecteur (implémentation par défaut)."""
        return CollectorCapabilities(
            supported_platforms=["linux"],
            min_confidence=0.95,
        )

    def classify_change(self, path: str, old_value: Any | None, new_value: Any | None) -> Severity:
        """Classifier la sévérité d'un changement (implémentation par défaut: info)."""
        return Severity.INFO
```

```python
@dataclass(frozen=True, slots=True)
class CollectorResult:
    data: dict                              # Données brutes (non normalisées)
    errors: list[CollectorError]            # Erreurs non bloquantes rencontrées
    execution_time_ms: float                # Temps d'exécution en ms
    raw_output: str | None                  # Sortie brute (pour debug)

@dataclass(frozen=True, slots=True)
class CollectorCapabilities:
    supported_platforms: list[str]
    min_confidence: float
    known_inconsistencies: list[str] = field(default_factory=list)

class Severity(StrEnum):
    CRITICAL = "critical"
    WARNING = "warning"
    INFO = "info"
```

**Format JSON produit :** Ce module ne produit pas de JSON directement. Il définit les structures qui seront sérialisées en phase NORMALIZE.

**Erreurs possibles :** Aucune (classe de définition).

**Performances attendues :** Négligeable.

---

### 3.3 `core/system_adapter.py`

**Rôle :** **Couche d'abstraction unique** entre le code (collecteurs) et le système d'exploitation. Intercepte et valide **TOUS** les appels système. C'est le **seul** point du projet qui exécute des commandes externes ou lit des fichiers système.

**Décision d'architecture :** Conforme à la Décision #8 (durcissement complet).

**Dépendances :** `subprocess`, `pathlib`, `core/exceptions.py`, `signal`.

**Whitelist ALLOWED_COMMANDS :**

```python
ALLOWED_COMMANDS: frozenset[str] = frozenset({
    "systemctl", "docker", "ip", "journalctl", "ss",
    "apt", "dpkg", "ufw", "iptables", "smartctl",
    "auditctl", "crontab", "openssl", "lsblk", "df",
    "sensors", "nproc", "hostname", "uname", "ls",
    "cat", "grep", "awk", "cut", "sort", "uniq",
    "wc", "find", "stat", "id", "whoami", "uptime",
    "free", "mount", "ps",
})
```

> **Règle absolue :** `shell=True` est **interdit** dans tous les appels `subprocess`.

---

**Sécurité — Couche 1 : Whitelist de commande**

Le premier mot de toute commande est validé contre `ALLOWED_COMMANDS`. Si la commande n'est pas dans la whitelist → `ForbiddenCommandError` immédiate, le run est interrompu.

---

**Sécurité — Couche 2 : Validation stricte des arguments (Décision #8)**

Tous les éléments de `args` sont validés avant exécution via regex `^[a-zA-Z0-9./_:-]+$`. Tout métacaractère shell suspect (`` &`$\|><"' ;$( ``) → `ForbiddenCommandError`.

---

**Sécurité — Couche 3 : Whitelist des chemins (Décision #8)**

```python
_ALLOWED_PROC_PREFIXES: frozenset[str] = frozenset({
    "/proc/", "/sys/", "/sys/class/net/",
})
```

Chemin hors whitelist → `ForbiddenCommandError` immédiate. Empêche la lecture de `/etc/shadow`, `/root/.ssh`, etc.

---

**Cache : Sécurisé et basé sur mtime (Décision #8)**

```python
def get_cached(self, path: Path) -> Any:
    key = (path, os.stat(path).st_mtime)  # Invalidation automatique
    if key in self._cache:
        return self._cache[key]
```

**Pas de cache pour `run_command()`** — chaque appel reflète l'état réel du système.

---

**Validation PID — Technique POSIX standard**

```python
os.kill(pid, 0)              # ✅ Standard POSIX — lève OSError si PID mort/zombie
os.kill(pid, signal.SIG_DFL) # ❌ Incorrect — SIG_DFL n'est pas un signal réel
```

---

**Méthodes publiques :**

| Méthode | Rôle | Retour |
|---|---|---|
| `run_command(cmd, args, timeout)` | Commande whitelistée + args validés | `CommandResult` |
| `read_proc_file(path)` | Lit un fichier `/proc/` whitelisté | `ProcFileResult` |
| `read_sys_file(path)` | Lit un fichier `/sys/` whitelisté | `ProcFileResult` |
| `list_directory(path)` | Liste un répertoire whitelisté | `list[str]` |
| `check_tool_available(tool)` | Vérifie qu'un outil est installé | `bool` |

---

**Erreurs possibles :**

| Erreur | Cause | Comportement |
|---|---|---|
| `ForbiddenCommandError` | Commande/argument/chemin hors whitelist | **Bloquant** — run interrompu, log FATAL, exit 1 |
| `subprocess.TimeoutExpired` | Commande dépasse le timeout | Log WARNING, `CollectorError` dans le résultat |
| `FileNotFoundError` | Fichier `/proc/` manquant | Log WARNING, données omises |
| `PermissionError` | Accès `/proc/sys/` sans root | Log WARNING, champ ignoré |

---

**Performances attendues :** Cache `(path, mtime)` pour `/proc/`/`/sys/`. Lectures répétées du même fichier = une seule exécution par run.

### 3.4 `core/event_bus.py`

**Rôle :** Bus d'événements synchrone in-process. Permet la communication découplée entre les modules sans dépendances directes. Ouvre la voie à l'extensibilité V2+ (webhooks, notifications, déclenchement LLM).

**Entrées :** Événements émis par les modules.

**Sorties :** Notification de tous les subscribers enregistrés.

**Dépendances :** `dataclasses`, `datetime`, `typing`.

**Implémentation :**

```python
@dataclass(frozen=True, slots=True)
class Event:
    event_type: str                    # ex: "collector.finished"
    payload: dict                      # Données de l'événement
    timestamp_utc: str                 # ISO8601 (ex: "2026-07-03T16:34:00Z")

class EventBus:
    _subscribers: dict[str, list[Callable[[Event], None]]]

    def subscribe(self, event_type: str, handler: Callable[[Event], None]) -> None:
        """Enregistre un handler pour un type d'événement."""

    def emit(self, event: Event) -> None:
        """Émet un événement — appelle synchroniquement tous les handlers."""
```

**Types d'événements :**

| Type | Sévérité | Payload clé | Émis par |
|---|---|---|---|
| `run.started` | INFO | `run_id`, `timestamp` | `pipeline.py` |
| `run.finished` | INFO | `run_id`, `duration_ms`, `changes_count`, `collectors_run` | `pipeline.py` |
| `run.failed` | CRITICAL | `run_id`, `error_type`, `error_message` | `pipeline.py` |
| `collector.started` | INFO | `collector_name`, `run_id` | `pipeline.py` |
| `collector.finished` | INFO | `collector_name`, `duration_ms`, `data_size` | `pipeline.py` |
| `collector.failed` | WARNING | `collector_name`, `error_type`, `error_message` | `pipeline.py` |
| `change.detected` | dépend de severity | `change_type`, `collector_name`, `severity`, `path`, `old_hash`, `new_hash` | `diff_engine.py` |
| `security.secret_redacted` | WARNING | `path`, `secret_type` | `sanitizer.py` |

**Performances attendues :** Distribution synchrone — chaque handler est appelé dans le thread courant avant le retour de `emit()`. L'ajout de handlers ne doit pas dégrader les performances de plus de 5 %.

---

### 3.5 `core/registry.py`

**Rôle :** Registre singleton des collecteurs découverts dynamiquement. Gère l'enregistrement, la découverte et l'accès aux collecteurs.

**Entrées :** Modules découverts via `pkgutil.iter_modules()` sur `collectors/`.

**Sorties :** Liste ordonnée de collecteurs instanciés.

**Dépendances :** `pkgutil`, `importlib`, `typing`, `core/base_collector.py`.

**Découverte automatique :**

```python
class Registry:
    _collectors: dict[str, type[BaseCollector]]

    @classmethod
    def discover(cls) -> None:
        """Découvre automatiquement tous les collecteurs dans collectors/."""
        for importer, name, ispkg in pkgutil.iter_modules(["collectors"]):
            module = importer.find_module(name).load_module(name)
            # Le décorateur @register_collector a déjà enregistré la classe
```

**Méthodes publiques :**

| Méthode | Rôle |
|---|---|
| `discover()` | Lance la découverte automatique |
| `register(name, cls)` | Enregistre une classe (appelé par le décorateur `@register_collector`) |
| `get_collector(name)` | Renvoie une instance du collecteur par nom |
| `list_collectors()` | Renvoie la liste de tous les noms de collecteurs enregistrés |

**Dépendancescycliques évitées :** `collectors/__init__.py` importe de `core.base_collector` après que le décorateur soit défini, mais avant l'appel à `discover()`.

**Erreurs possibles :**

| Erreur | Cause | Comportement |
|---|---|---|
| `CollectorNotFoundError` | Collecteur non enregistré | Log WARNING, collecteur ignoré |
| `DuplicateCollectorError` | Deux collecteurs même nom | Log WARNING, premier conservé |

**Performances attendues :** Découverte < 50 ms, impact mémoire négligeable.

---

### 3.6 `core/pipeline.py`

**Rôle :** Orchestrateur principal. Coordonne les 4 phases dans l'ordre, gère les erreurs globales, expose les statistiques de run.

**Entrées :** Configuration validée, lockfile acquis.

**Sorties :** JSON écrits dans `knowledge/`, `changes/` et `history/`. Événements émis sur l'EventBus.

**Dépendances :** `core/registry.py`, `core/event_bus.py`, `core/system_adapter.py`, `core/diff_engine.py`, `core/knowledge_store.py`, `core/logger.py`.

**Flux détaillé par phase :**

```
Phase COLLECT :
  → registry.discover()
  → registry.list_collectors() → pour chaque collecteur (séquentiel) :
       system_adapter.run_command() via le collecteur
       CollectorResult → emit("collector.finished")
       Stats cumulées (execution_time_ms)

Phase NORMALIZE :
  → schemas.py : validation Pydantic de chaque CollectorResult.data
  → hashing.compute_json_hash() : SHA256 sur JSON canonique
  → sanitizer.sanitize() : nettoyage anti-secrets
  → Assemblage du JSON final avec métadonnées

Phase COMPARE :
  → knowledge_store.read_manifest()
  → Pour chaque collecteur normalisé :
       diff_engine.compare(old_data, new_data)
       → Émet change.detected si hash différent
       → Écrit changes/<timestamp>.json
  → Écrit changes/manifest.json

Phase KNOWLEDGE BASE :
  → Pour chaque collecteur :
       knowledge_store.write_knowledge()
       history_store.rotate_and_prune()
  → Écrit knowledge/manifest.json
  → Purge changes/ (FIFO retention)
  → emit("run.finished")
```

**Statistiques de run :**

```python
@dataclass(frozen=True, slots=True)
class PipelineStats:
    run_id: str                        # UUID4 généré au démarrage
    started_at: str                    # ISO8601
    finished_at: str | None
    duration_ms: float
    collectors_run: int
    collectors_failed: int
    changes_detected: int
    memory_peak_mb: float
```

**Performances attendues :** Exécution complète < 30 secondes sur un serveur standard. Gestion propre des timeouts collecteurs.

---

### 3.7 `core/config_loader.py`

**Rôle :** Charge le fichier `config.yaml`, valide sa structure et ses valeurs avec Pydantic, expose un objet config typé accessible à tous les modules.

**Entrées :** Fichier `config.yaml` (chemin par défaut ou passé en argument).

**Sorties :** Objet `AICollectorConfig` (Pydantic BaseModel).

**Dépendances :** `pydantic`, `yaml`, `pathlib`, `core/exceptions.py`.

**Modèle Pydantic :**

```python
```python
class AICollectorConfig(BaseModel):
    class RetentionConfig(BaseModel):
        history_versions: int = 50         # Versions conservées par collecteur dans history/
        changes_entries: int = 200          # Entrées max dans changes/ (purge FIFO)
        logs_days: int = 30                 # Jours de rétention des logs

    class PathsConfig(BaseModel):
        # --- Chemins FHS (mode production) ---
        # En mode dev (AICOLLECTOR_ROOT défini), ces chemins sont préfixés
        base_dir: Path = Path("/var/lib/aicollector")
        config_dir: Path = Path("/etc/aicollector")
        cache_dir: Path = Path("/var/cache/aicollector")
        log_dir: Path = Path("/var/log/aicollector")
        lockfile_path: Path = Path("/run/aicollector/aicollector.lock")
        # Sous-répertoires (relatifs à base_dir)
        knowledge_subdir: str = "knowledge"
        history_subdir: str = "history"
        changes_subdir: str = "changes"
        cache_subdir: str = "cache"

    class SchedulerConfig(BaseModel):
        frequency_cron: str = "0 */2 * * *"  # Toutes les 2h par défaut
        use_systemd_timer: bool = False       # cron par défaut, systemd en option
        systemd_unit_dir: Path = Path("/etc/systemd/system")

    class CollectorsConfig(BaseModel):
        enabled: list[str] = []              # Liste blanche — vide = tous actifs
        disabled: list[str] = []              # Liste noire — vide = aucun désactivé
        timeout_seconds: int = 30              # Timeout par collecteur
        parallel: bool = False                # Exécution parallèle (V1.1+)
        root_required_behavior: Literal["skip", "warn", "fail"] = "skip"

    class SecurityConfig(BaseModel):
        allowed_commands: list[str]           # Whitelist explicite (lecture seule)
        exclude_paths: list[str]              # Chemins interdits de lecture
        redact_patterns: list[dict]           # Patterns de secrets à sanitis

    server_uuid: str | None = None           # Généré au premier run, persisté
    logging_level: Literal["DEBUG", "INFO", "WARNING", "ERROR"] = "INFO"

    class Config:
        extra = "forbid"
```

**Règles de validation :**

- `retention.history_versions` doit être entre 1 et 1000
- `retention.changes_entries` doit être entre 1 et 10 000
- `retention.logs_days` doit être entre 1 et 365
- `collectors.timeout_seconds` doit être entre 1 et 3600
- `collectors.root_required_behavior` doit être l'un de : `skip`, `warn`, `fail`
- `logging_level` doit être l'un des niveaux valides (DEBUG, INFO, WARNING, ERROR)
- Tout champ manquant reçoit sa valeur par défaut déclarée dans le modèle Pydantic
- Tout champ inattendu provoque une `ValidationError` (via `extra = "forbid"`)


| Erreur | Cause | Comportement |
|---|---|---|
| `ConfigFileNotFoundError` | Fichier absent | FATAL — exit code 1 |
| `ConfigValidationError` | YAML syntax error ou validation Pydantic fail | FATAL — exit code 1 avec message détaillé |

**Performances attendues :** Chargement < 50 ms, validation négligeable.

---

### 3.8 `core/schemas.py`

**Rôle :** Définit les modèles Pydantic (RootModel / BaseModel) pour la validation et la sérialisation des JSON produits par les collecteurs. Chaque collecteur utilise un sous-modèle dédié.

**Entrées :** Données brutes issues de `CollectorResult.data`.

**Sorties :** Modèles Pydantic validés prêts pour sérialisation JSON.

**Dépendances :** `pydantic`.

**Structure des modèles (exemple pour `CPUCollectorSchema`) :**

```python
class CPUCollectorSchema(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    # --- Champs obligatoires (communs à tous les collecteurs) ---
    schema_version: str = Field(default="1.0", pattern=r"^\d+\.\d+$")
    collector_version: str
    server_uuid: str
    timestamp_utc: str = Field(pattern=r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")
    source: str = "cpu"
    hash: str  # SHA256

    # --- Champs spécifiques au collecteur CPU ---
    content: CPUContent = Field(description="Données normalisées du CPU")

    # --- Champs optionnels ---
    confidence_score: float | None = Field(default=None, ge=0.0, le=1.0)
    dependencies: list[str] = Field(default_factory=list)
    inconsistencies_detected: list[str] = Field(default_factory=list)
    capabilities: CPUCapabilities | None = None


class CPUContent(BaseModel):
    model: str
    architecture: str
    cores_physical: int
    cores_logical: int
    threads_per_core: float
    frequency_mhz: float | None
    frequency_max_mhz: float | None
    cpu_flags: list[str]
    load_average_1m: float
    load_average_5m: float
    load_average_15m: float
    usage_percent: float | None
    temperature_celsius: float | None
```

> Chaque collecteur a son propre sous-modèle `Content`. Le modèle parent (`CollectorSchema`) commun à tous contient les champs obligatoires (`source`, `timestamp`, `version`). Les champs de contenu sont définis par chaque sous-modèle. La validation se fait via le registre dynamique de schémas : au chargement, `validate_knowledge_json()` retrouve le bon schéma dans le registre à partir du champ `source` et valide les données.

**Versioning de schéma :** Chaque collecteur incrémente son `schema_version` lors de modifications de structure. Le schéma lui-même est versionné via un champ `schema_version` dans chaque JSON produit. Un historique des versions de schéma est maintenu dans ce document.

**Performances attendues :** Validation < 10 ms par collecteur.

---

### 3.9 `core/logger.py`

**Rôle :** Logger structuré NDJSON avec intégration EventBus. Journalise tous les événements du pipeline dans `/var/log/aicollector/aicollector.log`.

**Décision d'architecture :** Conforme à la Décision #9 (niveaux séparés, atomicité NDJSON, sanitization, contextualisation).

**Dépendances :** `logging`, `logging.handlers`, `json`, `datetime`, `pathlib`, `core/event_bus.py`.

---

**Configuration par défaut :**

- Rotation : quotidienne (`TimedRotatingFileHandler`, `when='midnight'`, `backupCount=7`)
- Console : `INFO` (sortie stderr)
- Fichier : `DEBUG` (capture tout)

**Correction du niveau root :**

```python
logger.setLevel(logging.DEBUG)  # Ne filtre rien — délègue aux handlers
console_handler.setLevel(logging.INFO)
file_handler.setLevel(logging.DEBUG)
```

Le logger root est configuré au niveau le plus bas (`DEBUG`) pour ne jamais filtrer. Chaque handler définit son propre seuil de日志级别.

---

**Protection NDJSON (Décision #9)**

Le `_NDJSONFormatter` échappe tous les caractères de contrôle dans les valeurs avant sérialisation :

- `
` → `
`, `
` → `
`
- Backslashes, guillemets et caractères de contrôle échappés
- `default=str` pour les objets non-sérialisables (évite les crashes sur les types exotiques)

Garantie : une ligne = un objet JSON valide, même si un collecteur logue des données binaires ou des sauts de ligne.

---

**Sanitization des credentials (Décision #9)**

Le logger intègre un sanitizer appliqué à toutes les valeurs avant sérialisation :

```python
CREDENTIAL_PATTERNS = [
    r"password=\S+",
    r"token=\S+",
    r"secret=\S+",
    r"api_key=\S+",
    r"Bearer [a-zA-Z0-9_-]+",
]
```

Toute valeur correspondant à un pattern → remplacée par `***REDACTED***`.

---

**Contextualisation obligatoire (Décision #9)**

Chaque ligne NDJSON inclut automatiquement :

```json
{
  "timestamp": "2026-07-09T21:30:00.000Z",
  "level": "INFO",
  "event_type": "collector.finished",
  "run_id": "a1b2c3d4-...",
  "module": "pipeline",
  "collector_name": "cpu",
  "duration_ms": 142.5,
  "data_size_bytes": 2048
}
```

`run_id` injecté via `LoggerAdapter` wraps sans modifier la signature des appels. `timestamp` en ISO 8601 UTC.

---

**Format de log structuré :**

```json
{
  "timestamp": "2026-07-03T16:34:00.123Z",
  "level": "INFO",
  "event_type": "collector.finished",
  "run_id": "a1b2c3d4-...",
  "collector_name": "cpu",
  "duration_ms": 142.5,
  "data_size_bytes": 2048
}
```

| Type d'événement | Niveau de log |
|---|---|
| `run.started` / `run.finished` | INFO |
| `collector.started` / `collector.finished` | DEBUG |
| `collector.failed` | ERROR |
| `change.detected` (info) | INFO |
| `change.detected` (warning) | WARNING |
| `change.detected` (critical) | ERROR |
| `security.secret_redacted` | WARNING |
| `run.failed` | FATAL |

---

**Erreurs possibles :**

| Erreur | Cause | Comportement |
|---|---|---|
| `OSError` | Répertoire de log non accessible | Log WARNING sur stderr, continue |
| `UnicodeEncodeError` | Caractères non-ASCII dans le log | fallback UTF-8 (pas de `ensure_ascii=True`) |
| `json.JSONEncodeError` | Objet non-sérialisable | `default=str` — pas de crash |

### 3.10 `core/lockfile.py`

**Rôle :** Gestion du fichier de verrou anti-concurrent (`/run/aicollector/aicollector.lock`). Empêche les runs simultanés qui pourraient corrompre `knowledge/`.

**Décision d'architecture :** Conforme à la Décision #10 (atomicité POSIX, signal valide, gestion PID 1).

**Dépendances :** `os`, `signal`, `pathlib`, `datetime`, `core/exceptions.py`.

---

**Comportement :**

1. Tentative d'acquisition atomique via `os.open(path, os.O_CREAT|os.O_EXCL, 0o644)`
2. Si `O_EXCL` échoue (fichier existe) → lire le PID dormant
3. Vérifier si le processus est vivant : `os.kill(pid, 0)`
4. Si le processus est mort → acquisition forcée (écrase le lockfile périmé)
5. Si le processus est vivant → **verrouillage échoué** → `LockfileError`
6. Si PID = 1 → vérifier `/proc/1/cmdline` (PID 1 = init/systemd, ne peut pas mourir normalement)

**Structure du lockfile :**

```
PID:12345
TIMESTAMP:2026-07-03T16:34:00Z
RUN_ID:a1b2c3d4-e5f6-7890-abcd-ef1234567890
```

---

**Atomicité POSIX (Décision #10)**

```python
# ❌ Non atomique (race condition)
if self._lockfile.exists():
    pid = self._read_pid()
    if self._process_alive(pid):
        raise LockfileError(...)

# ✅ Atomique (O_EXCL)
try:
    fd = os.open(self._lockfile, os.O_CREAT | os.O_EXCL, 0o644)
except FileExistsError:
    # Fichier existe — vérifier si le processus est mort
    ...
```

`O_EXCL` garantit que l'appel échoue si le fichier existe déjà — atomicité complète à la primitive système.

> **Note :** Sur NFS, `O_EXCL` peut ne pas être atomique selon la config du serveur NFS. Pour les installations sur NFS shared storage, utiliser une alternative (flock, ou base de données).

---

**Validation PID — Technique POSIX standard (Décision #10)**

```python
os.kill(pid, 0)              # ✅ Standard POSIX — lève OSError si PID mort/zombie
os.kill(pid, signal.SIG_DFL) # ❌ Incorrect — SIG_DFL n'est pas un signal réel
```

---

**Gestion du PID 1 (Décision #10)**

```python
if pid == 1:
    cmdline = Path("/proc/1/cmdline").read_text(errors="ignore")
    if "systemd" not in cmdline and "init" not in cmdline:
        raise RuntimeError("PID 1 non systemd — lockfile potentiellement invalide")
```

PID 1 ne meurt jamais brutalement. Mais si `/proc/1/cmdline` ne contient pas `systemd` ou `init`, le lockfile est considéré comme invalide (corrompu) et réécrit.

---

**Erreurs possibles :**

| Erreur | Cause | Comportement |
|---|---|---|
| `LockfileError` | Run déjà en cours | Log FATAL, exit code 1 |
| `PermissionError` | `/run/aicollector/` non accessible en écriture | Log FATAL, exit code 1 |
| `OSError` | PID illisible (fichier corrompu) | Log WARNING, acquisition forcée |

---

**Performances attendues :** O(1) — une vérification de fichier + un appel `os.kill(pid, 0)`.
### 3.11 `core/hashing.py`

**Rôle :** Fonctions utilitaires pour le calcul de hash SHA256 sur des données JSON et fichiers.

**Entrées :** Dictionnaires Python, chaînes JSON, ou fichiers sur disque.

**Sorties :** Chaîne SHA256 préfixée `sha256:` (standard obligatoire du projet — Décision #6).

**Dépendances :** `hashlib`, `json`, `datetime` (pour le fallback encoder).

**Fonctions :**

```python
def compute_json_hash(data: dict[str, Any], *, canonical: bool = True) -> str:
    """
    Calcule le SHA256 d'un dictionnaire.
    Si canonical=True, le JSON est sérialisé avec sorted_keys=True
    et indent=None pour garantir la reproductibilité.
    Un encoder `default` gère les types non-JSON-natifs (datetime → isoformat,
    Path → str, set → str, Enum → str) pour éviter les TypeError silencieux.
    """
    if canonical:
        canonical_json = json.dumps(data, sort_keys=True, separators=(",", ":"))
    else:
        canonical_json = json.dumps(data, sort_keys=False)
    digest = hashlib.sha256(canonical_json.encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


def compute_file_hash(path: Path) -> str:
    """Calcule le SHA256 d'un fichier (pour les fichiers de base de connaissances)."""
```

**Canonicalisation :** Les espaces, l'ordre des clés et le format des nombres floats sont normalisés avant le calcul pour garantir que deux représentationslogiquement équivalentes produisent le même hash.

**Standard du préfixe `sha256:` (Décision #6) :** Tous les hash générés par le projet sont préfixés par `sha256:`. Cela permet d'identifier rapidement l'algorithme dans les manifestes et les logs, et prépare une future extensibilité multi-algorithme (sha512, blake2).

**Fallback encoder (Décision #6) :** Pour éviter les `TypeError` silencieux lors du hashing de données contenant des types non-JSON-natifs, `json.dumps` utilise un paramètre `default` qui convertit :
- `datetime` → `value.isoformat()` (ISO 8601, déterministe)
- `Path` → `str(value)` (dernier recours)
- Types résiduels → `str(value)` (permet au hash de calculer, avec warning si non déterministe)

**Performances attendues :** Hash < 5 ms pour un JSON de 1 Mo.

------

### 3.12 `core/diff_engine.py`

**Rôle :** Comparer deux dictionnaires et détecter les ajouts, suppressions et modifications entre deux snapshots.

**Entrées :** `old_data: dict`, `new_data: dict`.

**Sorties :** Liste de `Change` dataclasses.

**Dépendances :** `dataclasses`, `typing`, `core/hashing.py`.

**Structure des changements :**

```python
@dataclass(frozen=True, slots=True)
class Change:
    change_type: Literal["added", "removed", "modified"]
    path: str              # JSONPath : "content.processes[0].pid"
    old_value: Any | None
    new_value: Any | None
    severity: Severity
```

**Logique de comparaison :**

```
Comparaison récursive DeepDiff :
  - Si clé présente dans old ET new :
      - Si valeurs différentes → "modified" (appelle classifier)
      - Si les deux sont des dicts → récursion
  - Si clé présente uniquement dans new → "added"
  - Si clé présente uniquement dans old → "removed"
  - Si les deux sont des listes → comparaison index par index
  - Si les deux sont des dicts → récursion

Les valeurs sensibles sont masquées AVANT la comparaison
(le sanitizer a déjà passé avant).
```

**Erreurs possibles :** Aucune (comparaison pure, jamais d'I/O).

**Performances attendues :** Comparaison < 50 ms pour des JSON de 10 000 entrées.

---

### 3.13 `core/sanitizer.py`

**Rôle :** **Défense en profondeur** : parcourir récursivement toutes les données avant sérialisation JSON pour détecter et remplacer les secrets par `"***REDACTED***"`.

**Entrées :** Dictionnaires Python (données brutes ou normalisées).

**Sorties :** Copie du dictionnaire avec les secrets remplacés.

**Dépendances :** `re`, `dataclasses`, `typing`.

**Regex de détection des secrets :**

```python
SECRET_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("password", re.compile(r'(?i)(password|passwd|pwd)["\s:=]+["\']?([^"\'\s]+)', re.IGNORECASE)),
    ("api_key", re.compile(r'(?i)(api[_-]?key|apikey)["\s:=]+["\']?([^"\'\s]+)', re.IGNORECASE)),
    ("ssh_key", re.compile(r'(?i)(ssh[_-]?key|sshprivatekey)["\s:=]+["\']?([^"\'\s]+)', re.IGNORECASE)),
    ("token", re.compile(r'(?i)(bearer[_-]?token|auth[_-]?token|refresh[_-]?token)["\s:=]+["\']?([^"\'\s]+)', re.IGNORECASE)),
    ("private_key", re.compile(r'-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----')),
    ("docker_auth", re.compile(r'(?i)(docker[_-]?auth|docker[_-]?config)["\s:=]+["\']?([^"\'\s]+)', re.IGNORECASE)),
    ("aws_key", re.compile(r'AKIA[0-9A-Z]{16}')),
    ("credit_card", re.compile(r'\b\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}\b')),
    ("env_secret", re.compile(r'(?i)(SECRET|PASSWORD|API_KEY|TOKEN|PRIVATE)["\s]?=["\s]?["\']?([^"\'\s]+)', re.IGNORECASE)),
]
```

**Cheminsexclus :** Les valeurs associées aux chemins suivants sont systématiquement remplacées :

```python
FORBIDDEN_PATHS: set[str] = {
    "/etc/shadow",
    "/root/.ssh",
    "/home/*/.ssh",
    "/var/lib/docker/containers/*/config.v2.json",
}
```

**Comportement :** Lorsqu'un secret est détecté :

1. Remplacer la valeur par `"***REDACTED***"`
2. Émettre l'événement `security.secret_redacted` sur l'EventBus
3. Logger un WARNING avec le type de secret détecté et le chemin

**Performances attendues :** Sanitization < 10 ms pour un JSON de 1 Mo.

---

### 3.14 `core/knowledge_store.py`

**Rôle :** Gère la persistance et la lecture de `knowledge/`, la rotation physique FIFO de `history/`, et la purge FIFO de `changes/`.

**Entrées :** JSON normalisé (dict), configuration de rétention.

**Sorties :** Fichiers écrits sur disque de manière atomique, mise à jour atomique des manifestes.

**Dépendances :** `pathlib`, `json`, `os`, `tempfile`, `core/hashing.py`, `core/exceptions.py`.

**Méthodes publiques :**

| Méthode | Rôle |
|---|---|
| `write_knowledge(collector_name, data)` | Écrit `knowledge/<collector>.json` (atomiquement) et met à jour `knowledge/manifest.json` (atomiquement) |
| `read_knowledge(collector_name)` | Lit le dernier JSON connu d'un collecteur |
| `rotate_history(collector_name, data)` | Copie le JSON actuel via index-shift FIFO, purge le plus ancien si limite atteinte |
| `read_history(collector_name, version)` | Lit une version historique |
| `list_history_versions(collector_name)` | Renvoie la liste des versions disponibles |
| `write_change(change_data)` | Écrit `changes/<timestamp>.json` (atomiquement) et met à jour `changes/manifest.json` (atomiquement) |
| `prune_changes(keep: int)` | Purge FIFO — ne conserve que les `keep` derniers changements |
| `read_changes_manifest()` | Lit `changes/manifest.json` |
| `read_knowledge_manifest()` | Lit `knowledge/manifest.json` |

**Écriture atomique POSIX (Décision #7) :** Toutes les écritures de fichiers JSON utilisent le pattern atomic write :
1. Écriture dans un fichier temporaire via `tempfile.NamedTemporaryFile` (`mode='w'`, `encoding='utf-8'`, `delete=False`)
2. Fermeture du fichier temporaire
3. Renommage atomique via `os.replace(src, dst)`

Ce pattern garantit qu'en cas de crash, de signal d'interruption, ou de coupure disque, le fichier de destination est soit l'ancienne version intacte, soit la nouvelle version complète. Aucun JSON partiellement écrit ne peut exister sur disque.

**Rotation physique FIFO avec index-shift (Décision #7) :**

```
history/<collector>/
  ├── 0001.json   ← plus ancien (sera supprimé en premier par shift)
  ├── 0002.json
  ├── ...
  ├── 0049.json
  └── 0050.json  ← plus récent
```

Quand la limite `retention.history_versions` est atteinte :
1. Le fichier `0001` est supprimé (`os.remove`)
2. Tous les fichiers restants sont renommés : `0002→0001`, `0003→0002`, …, `max→max-1`
3. Le nouveau fichier est écrit en dernier slot (max)

L'index ne dérive jamais. Les noms de fichiers restent bornés (pas de `9999+`).

**Maintenance atomique des manifestes (Décision #7) :**
- `write_knowledge()` met à jour `knowledge/manifest.json` atomiquement après chaque écriture (incluant hash SHA256, timestamp, métadonnées)
- `write_change()` met à jour `changes/manifest.json` atomiquement après chaque détection de changement
- Les manifestes constituent l'index synthétique permettant à un agent IA de naviguer sans scanner le disque

**Erreurs possibles :**

| Erreur | Cause | Comportement |
|---|---|---|
| `KnowledgeWriteError` | Échec d'écriture disque | Log ERROR, warning non bloquant |
| `HistoryReadError` | Version historique introuvable | Log WARNING, renvoie None |

**Performances attendues :** Écriture < 100 ms par fichier, lecture < 20 ms.

---

### 3.15 `core/self_diagnostic.py`

**Rôle :** Auto-diagnostic au démarrage. Vérifie que l'environnement est compatible avant de lancer le pipeline.

**Entrées :** Aucune.

**Sorties :** `DiagnosticReport` (dataclass) avec liste d'avertissements et d'erreurs.

**Dépendances :** `sys`, `platform`, `pathlib`, `core/system_adapter.py`.

**Vérifications effectuées :**

| Vérification | Détail | Severity si échoué |
|---|---|---|
| Python version | Vérifie `>= 3.12` | FATAL |
| Répertoires accessibles | `knowledge/`, `changes/`, `history/`, `logs/` | FATAL |
| Permissions d'écriture | Tous les répertoires | FATAL |
| Outils système | `systemctl`, `docker`, `ss`, `smartctl` | WARNING (collecteur désactivé) |
| Platform | Vérifie `Linux` + `Ubuntu` | WARNING |
| Espace disque | Vérifie > 100 Mo disponibles | WARNING |
| Mémoire disponible | Vérifie > 100 Mo via `/proc/meminfo` | WARNING |

**Performances attendues :** Diagnostic < 2 secondes.

---

### 3.16 `core/exceptions.py`

**Rôle :** Hiérarchie complète des exceptions custom du projet.

**Dépendances :** `abc` (pour les classes de base), `datetime` (pour `cause`).

**Propriété publique `is_blocking` :** Toutes les exceptions de la hiérarchie exposent une propriété publique `is_blocking` (anciennement `_is_blocking`, renommé pour signaler qu'il fait partie de l'API publique). Cette propriété est interrogée par le pipeline (`pipeline.py`) pour décider si une erreur doit interrompre le run. La valeur par défaut est `False` (non bloquant) ; seule une minorité d'exceptions critiques la mettent à `True`.

**Chaînage `cause` :** Les exceptions propagées (ex: `CollectorError` contenant l'exception d'origine d'une commande `subprocess`) utilisent le pattern `cause` : `raise CollectorError("...") from e`. Le champ `cause` est exposé par la propriété `exception.__cause__` et loggué en DEBUG pour faciliter le diagnostic.

```
AICollectorError (base, is_blocking=False)
├── CollectorError (is_blocking=False)
│   ├── CollectorNotFoundError (is_blocking=False)
│   ├── CollectorTimeoutError (is_blocking=False)
│   └── CollectorPermissionError (is_blocking=False)
├── SystemAdapterError (is_blocking=False)
│   ├── ForbiddenCommandError (is_blocking=True)  ← BLOQUANT
│   ├── CommandExecutionError (is_blocking=False)  ← NON BLOQUANT (Décision #5)
│   └── ProcFileReadError (is_blocking=False)
├── ConfigError (is_blocking=True)  ← BLOQUANT
│   ├── ConfigFileNotFoundError (is_blocking=True)
│   └── ConfigValidationError (is_blocking=True)
├── PipelineError (is_blocking=True)  ← BLOQUANT
│   ├── PhaseError (is_blocking=True)
│   └── RunInterruptedError (is_blocking=True)
├── LockfileError (is_blocking=True)  ← BLOQUANT
├── SchemaValidationError (is_blocking=False)
├── KnowledgeStoreError (is_blocking=False)
│   ├── KnowledgeWriteError (is_blocking=False)
│   └── HistoryReadError (is_blocking=False)
└── EventBusError (is_blocking=False)
```

**Convention :** Toutes les exceptions customisent `__str__` pour afficher un message structuré `{ClassName}: {détail}`. Les exceptions critiques incluent un champ `exit_code`. Les exceptions propagées chainent la cause via `raise X from e`.

**Performances attendues :** Négligeable.

---

### 3.17 `collectors/` (package de collecteurs)

**Rôle :** Chaque collecteur est un **plugin indépendant** qui implements `BaseCollector`. Ils sont découverts dynamiquement via `pkgutil.iter_modules()` et auto-enregistrés via le décorateur `@register_collector`.

**Mécanisme d'enregistrement :**

```python
# collectors/__init__.py
from core.registry import Registry
from core.base_collector import BaseCollector

def register_collector(name: str):
    """Décorateur d'auto-enregistrement."""
    def deco(cls: type[BaseCollector]) -> type[BaseCollector]:
        cls.name = name  # Force le name
        Registry.register(name, cls)
        return cls
    return deco

# Chaque collecteur utilise le décorateur :
# @register_collector("cpu")
# class CPUCollector(BaseCollector):
#     ...
```

**Liste des collecteurs V1.0 :**

| Collecteur | Source principales | Requis root | Timeout |
|---|---|---|---|
| `system` | `hostname`, `uname`, `/etc/os-release` | Non | 10s |
| `cpu` | `/proc/cpuinfo`, `/proc/stat`, `/sys/devices/system/cpu/` | Non | 15s |
| `ram` | `/proc/meminfo`, `free` | Non | 10s |
| `storage` | `df -h`, `lsblk -J` | Non | 20s |
| `smart` | `smartctl -a` (par disque) | **Oui** | 60s |
| `network` | `ip addr`, `ip route`, `ss -tunap` | Non | 20s |
| `docker` | `docker ps -a`, `docker info --format '{{json .}}'` | **Oui** | 30s |
| `systemd_services` | `systemctl list-units --all --no-pager` | Non | 30s |
| `firewall` | `ufw status verbose`, `iptables -L -n` | **Oui** | 20s |
| `auditd` | `auditctl -s`, `/var/log/audit/audit.log` | **Oui** | 30s |
| `apt` | `dpkg-query --show --showformat='$i\t\$p\t\$v\t\$V\t\$s\t\$c\n'`, `apt list --upgradable 2>/dev/null` | Non | 40s |
| `users` | `getent passwd`, `getent group` | Non | 15s |
| `cron` | `crontab -l` (chaque utilisateur) | Non | 30s |
| `timers` | `systemctl list-timers --all` | Non | 20s |
| `ssl_certificates` | `openssl s_client`, fichiers dans `/etc/ssl/certs/` | Non | 40s |
| `syslogs` | `journalctl -n 1000 --no-pager` | Non | 30s |

**Règle d'ajout :** Tout nouveau collecteur doit :

1. Être dans un fichier `collectors/<nom>.py`
2. Hériter de `BaseCollector`
3. Porter le décorateur `@register_collector("<nom>")`
4. Implémenter `collect(self, system: SystemAdapter) -> CollectorResult`
5. Définir ses propres `schema_version` et `collector_version`
6. Implémenter `classify_change()` si le comportement par défaut ne convient pas

Aucun composant du pipeline ne doit être modifié. Seuls les points d'extension prévus (`schemas.py`, exporters, etc.) peuvent être étendus.

---

### 3.18 `collectors/apt.py`

**Rôle :** Collecter la liste exhaustive des paquets Debian/Ubuntu installés via `dpkg-query`, ainsi que les mises à jour disponibles via `apt list --upgradable`.

**Commande(s) source :**
- `dpkg-query --show --showformat='${db:Status-Abbrev}|${Package}|${Version}|${Installed-Size}|${binary:Field-Description}' 2>/dev/null`
- `apt list --upgradable 2>/dev/null` (optionnel — avertissement si dpkg-query indisponible)

**Détails de l'implémentation :**

**Choix d'implémentation :** Plutôt que d'analyser `dpkg -l` (dont la largeur des colonnes est affectée par la variable d'environnement `COLUMNS`, ce qui tronque les noms de paquets longs), le collecteur APT exploite **`dpkg-query`** avec l'option `--show` combinée à `--showformat`. Cette approche offre :

1. **Robuste et prévisible :** Délimitation stricte des champs par un séparateur de type tabulation (`	`), aucun risque de décalage de colonnes.
2. **Filtrage à la source :** Ne cible que les paquets avec `db:Status-Abbrev = ii` (état `installed`), excluant les résidus de configuration (`rc`) ou les paquets purgés.
3. **Uniqueness garantie :** Chaque ligne correspond à un et un seul enregistrement de paquet — aucune ambiguïté.
4. **Performant :** Lecture directe de la base dpkg en une seule commande `O(n)` — aucun parsing intermédiaire.

**Format du `--showformat` :**

```
db:Status-Abbrev|Package|Version|Installed-Size|binary:Field-Description
```

- Champs séparés par `|` (pipe) — caractère rarissime dans les noms de paquets
- `db:Status-Abbrev` (2 caractères) : état du paquet dans la base dpkg
  - `ii` = installé (Installed)
  - `iU` = décompressé mais non-configuré
  - `rc` =残留 de configuration (Removed, Config-files remain)
  - `pn` = packet not found
- `Package` : nom du paquet (peut contenir des `-` et des `_`)
- `Version` : version EPoch:Debian-Release du paquet installé
- `Installed-Size` : taille 安装ée en Ko (entier)
- `binary:Field-Description` : champ description binaire (première ligne uniquement = synopsis court)

**Algorithme de collecte :**

```
1. Exécuter dpkg-query avec --showformat pipe-delimited
2. Pour chaque ligne non-vide :
   a. Split sur '|'
   b. Vérifier que db:Status-Abbrev == 'ii' (sinon → skip)
   c. Extraire les 5 champs dans l'ordre
   d. Parser Installed-Size en entier
   e. Append au tableau packages
3. Statistiques : total_packages = len(packages)
4. Si apt list --upgradable disponible :
   a. Parser la sortie apt list (format: "paquet/version [upgradable from: x to: y]")
   b. Enrichir le champ status de chaque APTPackage correspondant
   c. Compter upgradable_packages
```

**Gestion des erreurs :**
- `dpkg-query: pas de候选人` → retourne `packages: []` avec `confidence_score = 0.0` et `inconsistencies_detected` = "dpkg database empty or dpkg-query not available"
- `apt list` échoue (non-root, ou apt mal configuré) → champ `upgradable_packages_list: []` avec avertissement dans `inconsistencies_detected`

**Paquets exclus par conception :**
- Tous les paquets dont `db:Status-Abbrev ≠ ii` (résidus de config, supprimés, etc.)
- Les_virtual_packages_ listedés par `dpkg` mais non traçables par `dpkg-query --show`

**Performances attendues :**
- Exécution < 5 s sur un serveur standard (600-2000 paquets)
- Mémoire : proportionnelle au nombre de paquets (~200 octets/par paquet → ~400 Ko pour 2000 paquets)
- Validation Pydantic < 10 ms

**Schéma JSON (`APTCollectorSchema`, schema_version 1.1) :**

```json
{
  "schema_version": "1.1",
  "collector_version": "1.0.0",
  "server_uuid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "timestamp_utc": "2026-07-14T11:00:00Z",
  "source": "apt",
  "hash": "sha256:...",
  "content": {
    "distribution": "Ubuntu 26.04 LTS",
    "total_packages": 1247,
    "installed_packages": 1198,
    "upgradable_packages": 3,
    "packages": [
      {
        "name": "openssl",
        "architecture": "amd64",
        "version": "3.0.13-0ubuntu3",
        "installed_version": "3.0.13-0ubuntu3",
        "size_bytes": 2097152,
        "description": "Secure Sockets and Transport Layer Security",
        "status": "installed"
      }
    ],
    "upgradable_packages_list": [
      {
        "name": "curl",
        "architecture": "amd64",
        "version": "8.12.1-2ubuntu3",
        "installed_version": "8.12.1-2ubuntu1",
        "size_bytes": 734003,
        "description": "command line tool for transferring data with URL syntax",
        "status": "upgradable"
      }
    ]
  },
  "confidence_score": 0.99,
  "dependencies": [],
  "inconsistencies_detected": [],
  "capabilities": {
    "supported_platforms": ["linux"],
    "min_confidence": 0.95,
    "known_inconsistencies": [
      "apt list requires root or sudo for full upgrade list",
      "virtual packages not listed by dpkg-query --show"
    ]
  }
}
```

---

## 4. Schéma des fichiers JSON

### 4.1 Structure commune (commune à TOUS les collecteurs)

Chaque fichier JSON produit par un collecteur respecte la structure suivante :

```json
{
  "schema_version": "1.0",        // OBLIGATOIRE — version du schéma Pydantic (string, format X.Y)
  "collector_version": "1.2.0",   // OBLIGATOIRE — version du collecteur lui-même
  "server_uuid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890", // OBLIGATOIRE — UUID du serveur (généré une fois)
  "timestamp_utc": "2026-07-03T16:34:00Z", // OBLIGATOIRE — ISO8601 UTC
  "source": "cpu",                // OBLIGATOIRE — nom du collecteur
  "hash": "sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855", // OBLIGATOIRE — SHA256 du contenu (hors hash lui-même)
  "content": {},                  // OBLIGATOIRE — données normalisées (typevar selon collecteur)
  "confidence_score": 0.98,       // OPTIONNEL — score de confiance 0.0-1.0
  "dependencies": ["ram", "system"], // OPTIONNEL — collecteurs dont dépendent les données
  "inconsistencies_detected": [], // OPTIONNEL — anomalies détectées non bloquantes
  "capabilities": {}              // OPTIONNEL — capacités du collecteur (voir CollectorCapabilities)
}
```

**Règles de validation :**

- `schema_version` : doit matcher `^\d+\.\d+$` (ex: `1.0`, `2.1`)
- `collector_version` : doit matcher le format sémantique `^\d+\.\d+\.\d+$`
- `server_uuid` : doit matcher `^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$`
- `timestamp_utc` : doit matcher `^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$`
- `source` : doit être dans la liste des collecteurs enregistrés
- `hash` : doit matcher `^sha256:[0-9a-f]{64}$`
- `confidence_score` : si présent, doit être `>= 0.0` et `<= 1.0`

### 4.2 Métadonnées de version de schéma

| Version de schéma | Collecteur(s) | Date d'introduction | Changements |
|---|---|---|---|
| 1.0 | Tous | V1.0 | Schéma initial |
| 1.1 | apt | V1.2 | Schéma initial du collecteur APT (programmes installés + mises à jour disponibles) |
| 1.2 | docker | V1.1 (future) | Ajout champ `networks` dans le content |

### 4.3 Exemples JSON complets

---

#### Exemple 1 : `knowledge/cpu.json`

```json
{
  "schema_version": "1.0",
  "collector_version": "1.0.3",
  "server_uuid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "timestamp_utc": "2026-07-03T16:34:00Z",
  "source": "cpu",
  "hash": "sha256:7f83b1657ff1fc53b92dc18148a1d65d8b1c7e9f3a5d2c3b1a0f9e8d7c6b5a4",
  "content": {
    "model": "Intel(R) Xeon(R) CPU E5-2680 v4 @ 2.40GHz",
    "architecture": "x86_64",
    "cores_physical": 14,
    "cores_logical": 28,
    "threads_per_core": 2.0,
    "frequency_mhz": 2400.0,
    "frequency_max_mhz": 3300.0,
    "cpu_flags": [
      "fpu", "vme", "de", "pse", "tsc", "msr", "pae", "mce",
      "cx8", "apic", "bmask", "mtrr", "pge", "cflush", "dts",
      "acpi", "ht", "tm", "mmx", "fxsr", "sse", "sse2", "ss",
      "htt", "sse3", "pclmulqdq", "dtes64", "monitor", "ds_cpl",
      "vmx", "smx", "est", "tm2", "ssse3", "cx16", "xpr", "pdcm",
      "dca", "sse4.1", "sse4.2", "x2apic", "popcnt", "dea",
      "tsc_deadline_timer", "aes", "xsave", "avx", "f16c",
      "rdrand", "hypervisor"
    ],
    "load_average_1m": 0.42,
    "load_average_5m": 0.61,
    "load_average_15m": 0.73,
    "usage_percent": 15.3,
    "temperature_celsius": 48.5
  },
  "confidence_score": 0.99,
  "dependencies": ["system"],
  "inconsistencies_detected": [],
  "capabilities": {
    "supported_platforms": ["linux"],
    "min_confidence": 0.95,
    "known_inconsistencies": [
      "temperature may be null if lm-sensors not installed"
    ]
  }
}
```

---

#### Exemple 2 : `knowledge/network.json`

```json
{
  "schema_version": "1.0",
  "collector_version": "1.1.2",
  "server_uuid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "timestamp_utc": "2026-07-03T16:34:05Z",
  "source": "network",
  "hash": "sha256:b4e2d819a3c57b9f0e4d8a2c1b7e6f9a3d2c4b6e8f0a2c4d6e8f0a2b4c6d8e",
  "content": {
    "hostname": "srv-prod-01.internal",
    "domain": "internal",
    "interfaces": [
      {
        "name": "lo",
        "type": "loopback",
        "state": "UP",
        "mtu": 65536,
        "mac_address": "00:00:00:00:00:00",
        "ipv4": [{"address": "127.0.0.1", "netmask": "255.0.0.0", "broadcast": null}],
        "ipv6": [
          {"address": "::1/128", "netmask": "ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff"}
        ]
      },
      {
        "name": "eth0",
        "type": "ether",
        "state": "UP",
        "mtu": 9000,
        "mac_address": "52:54:00:12:34:56",
        "ipv4": [{"address": "10.0.2.15", "netmask": "255.255.255.0", "broadcast": "10.0.2.255"}],
        "ipv6": [],
        "speed_mbps": 1000,
        "duplex": "full"
      }
    ],
    "routes": [
      {
        "destination": "default",
        "gateway": "10.0.2.1",
        "interface": "eth0",
        "metric": 100
      },
      {
        "destination": "10.0.2.0/24",
        "gateway": null,
        "interface": "eth0",
        "metric": 100
      }
    ],
    "listening_ports": [
      {"protocol": "tcp", "local_address": "0.0.0.0", "port": 22, "process": "sshd"},
      {"protocol": "tcp", "local_address": "0.0.0.0", "port": 80, "process": "nginx"},
      {"protocol": "tcp", "local_address": "0.0.0.0", "port": 443, "process": "nginx"}
    ],
    "established_connections": 14,
    "dns_servers": ["10.0.2.3", "10.0.2.1"]
  },
  "confidence_score": 0.97,
  "dependencies": ["system"],
  "inconsistencies_detected": [
    "interface eth0 speed_mbps not available via ethtool — inferred from ethtool output"
  ],
  "capabilities": {
    "supported_platforms": ["linux"],
    "min_confidence": 0.90,
    "known_inconsistencies": [
      "speed_mbps may be null if ethtool not available",
      "established_connections is a snapshot at collect time"
    ]
  }
}
```

---

#### Exemple 3 : `knowledge/docker.json`

```json
{
  "schema_version": "1.0",
  "collector_version": "1.0.1",
  "server_uuid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "timestamp_utc": "2026-07-03T16:34:10Z",
  "source": "docker",
  "hash": "sha256:c5f9e2a7b3d8c1e4f6a2b9d0c3e5f7a1b4d6c8e0f2a4b6d8c0e2f4a6b8d0c2e",
  "content": {
    "docker_version": "27.2.0",
    "api_version": "1.44",
    "os": "linux",
    "kernel_version": "6.8.0-60-generic",
    "server_uuid": "d4e5f6a7-b8c9-d0e1-f2a3-b4c5d6e7f8a9",
    "containers_total": 8,
    "containers_running": 5,
    "containers_paused": 0,
    "containers_stopped": 3,
    "images_count": 12,
    "memory_limit_enabled": true,
    "swap_limit_enabled": true,
    "cgroup_driver": "systemd",
    "runtime": "runc",
    "containers": [
      {
        "id": "a1b2c3d4e5f6",
        "short_id": "a1b2c3d4e5f6",
        "name": "nginx-proxy",
        "image": "nginx:1.25-alpine",
        "status": "running",
        "created": "2026-03-15T10:30:00Z",
        "state": {
          "status": "running",
          "running": true,
          "paused": false,
          "restarting": false,
          "exit_code": 0,
          "started_at": "2026-07-03T06:00:00Z"
        },
        "ports": [
          {"private_port": 80, "public_port": 8080, "protocol": "tcp", "type": "host"},
          {"private_port": 443, "public_port": 8443, "protocol": "tcp", "type": "host"}
        ],
        "labels": {
          "managed_by": "docker-compose",
          "project": "proxy"
        },
        "networks": ["proxy-net", "default"],
        "mounts": [
          {"type": "bind", "source": "/data/nginx", "destination": "/var/cache/nginx", "mode": "rw"}
        ],
        "resource_limits": {
          "cpu_shares": 512,
          "memory_limit_bytes": 536870912,
          "memory_reservation_bytes": 134217728
        }
      },
      {
        "id": "b2c3d4e5f6a7",
        "short_id": "b2c3d4e5f6a7",
        "name": "postgres-db",
        "image": "postgres:16-alpine",
        "status": "running",
        "created": "2026-04-20T14:00:00Z",
        "state": {
          "status": "running",
          "running": true,
          "paused": false,
          "restarting": false,
          "exit_code": 0,
          "started_at": "2026-07-03T06:00:05Z"
        },
        "ports": [
          {"private_port": 5432, "public_port": null, "protocol": "tcp", "type": "bridge"}
        ],
        "labels": {
          "managed_by": "docker-compose",
          "project": "database"
        },
        "networks": ["default"],
        "mounts": [
          {"type": "volume", "source": "postgres_data", "destination": "/var/lib/postgresql/data", "mode": "rw"}
        ],
        "resource_limits": {
          "cpu_shares": 1024,
          "memory_limit_bytes": 2147483648,
          "memory_reservation_bytes": 1073741824
        }
      },
      {
        "id": "c3d4e5f6a7b8",
        "short_id": "c3d4e5f6a7b8",
        "name": "redis-cache",
        "image": "redis:7.2-alpine",
        "status": "exited",
        "created": "2026-05-01T09:00:00Z",
        "state": {
          "status": "exited",
          "running": false,
          "paused": false,
          "restarting": false,
          "exit_code": 137,
          "started_at": "2026-07-01T06:00:00Z",
          "finished_at": "2026-07-01T12:30:00Z"
        },
        "ports": [],
        "labels": {
          "managed_by": "docker-compose",
          "project": "cache"
        },
        "networks": ["default"],
        "mounts": [
          {"type": "volume", "source": "redis_data", "destination": "/data", "mode": "rw"}
        ],
        "resource_limits": {
          "cpu_shares": 256,
          "memory_limit_bytes": 536870912,
          "memory_reservation_bytes": 67108864
        }
      }
    ],
    "networks": [
      {"name": "bridge", "driver": "bridge", "scope": "local", "internal": false},
      {"name": "proxy-net", "driver": "bridge", "scope": "local", "internal": false}
    ],
    "volumes": [
      {"name": "postgres_data", "driver": "local", "mountpoint": "/var/lib/docker/volumes/postgres_data/_data"},
      {"name": "redis_data", "driver": "local", "mountpoint": "/var/lib/docker/volumes/redis_data/_data"}
    ]
  },
  "confidence_score": 0.99,
  "dependencies": [],
  "inconsistencies_detected": [
    "container redis-cache exited with code 137 — may indicate OOM kill or SIGKILL"
  ],
  "capabilities": {
    "supported_platforms": ["linux"],
    "min_confidence": 0.95,
    "known_inconsistencies": [
      "requires docker daemon running and user in docker group or root",
      "container exit codes are snapshot at collect time"
    ]
  }
}
```

---



---

#### Exemple 4 : `knowledge/apt.json`

```json
{
  "schema_version": "1.1",
  "collector_version": "1.0.0",
  "server_uuid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "timestamp_utc": "2026-07-14T11:00:00Z",
  "source": "apt",
  "hash": "sha256:9a8b7c6d5e4f3a2b1c0d9e8f7a6b5c4d3e2f1a0b9c8d7e6f5a4b3c2d1e0f9a",
  "content": {
    "distribution": "Ubuntu 26.04 LTS",
    "total_packages": 1247,
    "installed_packages": 1198,
    "upgradable_packages": 3,
    "packages": [
      {
        "name": "openssl",
        "architecture": "amd64",
        "version": "3.0.13-0ubuntu3",
        "installed_version": "3.0.13-0ubuntu3",
        "size_bytes": 2097152,
        "description": "Secure Sockets and Transport Layer Security",
        "status": "installed"
      },
      {
        "name": "curl",
        "architecture": "amd64",
        "version": "8.12.1-2ubuntu3",
        "installed_version": "8.12.1-2ubuntu1",
        "size_bytes": 734003,
        "description": "command line tool for transferring data with URL syntax",
        "status": "upgradable"
      },
      {
        "name": "python3",
        "architecture": "amd64",
        "version": "3.13.1-1ubuntu3",
        "installed_version": "3.13.1-1ubuntu3",
        "size_bytes": 45875200,
        "description": "interpreter of the Python programming language",
        "status": "installed"
      }
    ],
    "upgradable_packages_list": [
      {
        "name": "curl",
        "architecture": "amd64",
        "version": "8.12.1-2ubuntu3",
        "installed_version": "8.12.1-2ubuntu1",
        "size_bytes": 734003,
        "description": "command line tool for transferring data with URL syntax",
        "status": "upgradable"
      },
      {
        "name": "tzdata",
        "architecture": "all",
        "version": "2026a-0ubuntu3",
        "installed_version": "2025b-0ubuntu2",
        "size_bytes": 1536000,
        "description": "time zone and daylight-saving time data",
        "status": "upgradable"
      },
      {
        "name": "libssl3t64",
        "architecture": "amd64",
        "version": "3.0.13-0ubuntu3",
        "installed_version": "3.0.13-0ubuntu2",
        "size_bytes": 1945600,
        "description": "Secure Sockets and Transport Layer Security",
        "status": "upgradable"
      }
    ]
  },
  "confidence_score": 0.99,
  "dependencies": [],
  "inconsistencies_detected": [],
  "capabilities": {
    "supported_platforms": ["linux"],
    "min_confidence": 0.95,
    "known_inconsistencies": [
      "apt list --upgradable requires root or sudo for complete upgrade list",
      "virtual packages are not listed by dpkg-query --show"
    ]
  }
}
```

---

## 5. Format de `manifest.json`

### 5.1 Rôle

Le fichier `knowledge/manifest.json` est l'**index global** de la base de connaissances. Il sert à :

- Connaître l'état de chaque collecteur (dernier hash, dernière exécution)
- Détecter rapidement les collecteurs actifs/inactifs/sans données
- Permettre à un agent IA de naviguer dans la base de connaissances sans lire chaque fichier individuel
- Calculer un `global_hash` représentant l'état complet du serveur à un instant T

### 5.2 Structure

```json
{
  "schema_version": "1.0",
  "manifest_version": "1.0",
  "server_uuid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "generated_at": "2026-07-03T16:34:30Z",
  "run_id": "f0e1d2c3-b4a5-6789-0abc-def012345678",
  "collectors": {
    "cpu": {
      "file": "cpu.json",
      "schema_version": "1.0",
      "collector_version": "1.0.3",
      "last_hash": "sha256:7f83b1657ff1fc53b92dc18148a1d65d8b1c7e9f3a5d2c3b1a0f9e8d7c6b5a4",
      "last_run": "2026-07-03T16:34:00Z",
      "last_change": "2026-07-01T08:00:00Z",
      "run_count": 144,
      "status": "active"
    },
    "network": {
      "file": "network.json",
      "schema_version": "1.0",
      "collector_version": "1.1.2",
      "last_hash": "sha256:b4e2d819a3c57b9f0e4d8a2c1b7e6f9a3d2c4b6e8f0a2c4d6e8f0a2b4c6d8e",
      "last_run": "2026-07-03T16:34:05Z",
      "last_change": null,
      "run_count": 144,
      "status": "active"
    },
    "docker": {
      "file": "docker.json",
      "schema_version": "1.0",
      "collector_version": "1.0.1",
      "last_hash": "sha256:c5f9e2a7b3d8c1e4f6a2b9d0c3e5f7a1b4d6c8e0f2a4b6d8c0e2f4a6b8d0c2e",
      "last_run": "2026-07-03T16:34:10Z",
      "last_change": "2026-07-02T06:00:00Z",
      "run_count": 144,
      "status": "active"
    }
  },
  "global_hash": "sha256:a1b2c3d4e5f6a7b8c9d0e1f2a3b4c5d6e7f8a9b0c1d2e3f4a5b6c7d8e9f0a1b2",
  "run_stats": {
    "total_runs": 144,
    "last_run_duration_ms": 8240.5,
    "avg_run_duration_ms": 7890.2,
    "total_changes_detected": 412,
    "last_change_detected_at": "2026-07-02T06:00:00Z"
  }
}
```

### 5.3 Champs du manifest.json

| Champ | Type | Obligatoire | Description |
|---|---|---|---|
| `schema_version` | string | ✅ | Version du format de manifeste |
| `manifest_version` | string | ✅ | Version du schéma du manifeste lui-même |
| `server_uuid` | string | ✅ | UUID du serveur (doit correspondre à tous les JSON) |
| `generated_at` | string (ISO8601) | ✅ | Timestamp de génération du manifest |
| `run_id` | string (UUID) | ✅ | UUID du run qui a généré ce manifest |
| `collectors` | dict | ✅ | Index par nom de collecteur |
| `collectors.<nom>.file` | string | ✅ | Nom du fichier JSON dans knowledge/ |
| `collectors.<nom>.schema_version` | string | ✅ | Version du schéma du collecteur |
| `collectors.<nom>.collector_version` | string | ✅ | Version du collecteur |
| `collectors.<nom>.last_hash` | string (SHA256) | ✅ | Hash SHA256 du dernier JSON connu |
| `collectors.<nom>.last_run` | string (ISO8601) | ✅ | Dernier timestamp de run |
| `collectors.<nom>.last_change` | string (ISO8601) | ❌ | Timestamp du dernier changement détecté (null si aucun) |
| `collectors.<nom>.run_count` | int | ✅ | Nombre total de runs réussis |
| `collectors.<nom>.status` | string | ✅ | `active`, `disabled`, `error`, `no_data` |
| `global_hash` | string (SHA256) | ✅ | Hash de tous les `last_hash` concaténés |
| `run_stats` | dict | ✅ | Statistiques agrégées des runs |
| `run_stats.total_runs` | int | ✅ | Nombre total de runs |
| `run_stats.last_run_duration_ms` | float | ✅ | Durée du dernier run |
| `run_stats.avg_run_duration_ms` | float | ✅ | Durée moyenne des runs |
| `run_stats.total_changes_detected` | int | ✅ | Nombre total de changements détectés |
| `run_stats.last_change_detected_at` | string (ISO8601) | ❌ | Timestamp du dernier changement (null si aucun) |

### 5.4 Calcul du `global_hash`

```python
def compute_global_hash(collectors_index: dict) -> str:
    """
    Le global_hash est le SHA256 de la concaténation triée
    de tous les last_hash des collecteurs actifs.
    """
    hashes = sorted([
        c["last_hash"]
        for c in collectors_index.values()
        if c["status"] == "active" and c.get("last_hash")
    ])
    concatenated = "||".join(hashes)
    return "sha256:" + hashlib.sha256(concatenated.encode()).hexdigest()
```

### 5.5 Gestion des versions

Le manifest suit son propre cycle de versionnage (`manifest_version`), indépendant des versions de schéma des collecteurs :

| `manifest_version` | Description |
|---|---|
| 1.0 | Format initial (V1.0) |
| 1.1 | Ajout du champ `run_stats.avg_run_duration_ms` |

Le `schema_version` du manifest suit le même pattern de MAJOR.MINOR.

---

## 6. Format de `changes.json`

### 6.1 Rôle

Les fichiers dans `changes/` constituent l'**historique des détections de changement**. Chaque exécution qui produit au moins un changement génère un fichier `<timestamp>.json`. L'objectif est de permettre à un agent IA de comprendre *quand* et *comment* le système a évolué entre deux runs.

### 6.2 Types d'événements de changement

| Type | Signification | Champs correspondants |
|---|---|---|
| `added` | Une nouvelle clé/valeur est apparue | `new_value` populated, `old_value = null` |
| `removed` | Une clé/valeur a disparu | `old_value` populated, `new_value = null` |
| `modified` | Une valeur a changé | `old_value` et `new_value` tous deux populated |
| `identical` | Aucune modification (existe mais pas journalisée) | Non applicable |

### 6.3 Structure d'un fichier de changement

```json
{
  "schema_version": "1.0",
  "change_id": "c3d4e5f6-a7b8-90ab-cdef-012345678901",
  "run_id": "f0e1d2c3-b4a5-6789-0abc-def012345678",
  "timestamp_utc": "2026-07-03T16:34:30Z",
  "collector": "docker",
  "severity": "warning",
  "summary": "3 containers changed state",
  "total_changes": 5,
  "changes": [
    {
      "type": "removed",
      "path": "content.containers[name=redis-cache]",
      "description": "Container redis-cache removed from docker list",
      "old_value_hash": "sha256:abc123...",
      "new_value_hash": null,
      "severity": "warning"
    },
    {
      "type": "modified",
      "path": "content.containers[name=nginx-proxy].state.started_at",
      "description": "Container nginx-proxy restarted",
      "old_value": "2026-07-01T06:00:00Z",
      "new_value": "2026-07-03T06:00:00Z",
      "old_value_hash": "sha256:def456...",
      "new_value_hash": "sha256:ghi789...",
      "severity": "info"
    },
    {
      "type": "added",
      "path": "content.containers[name=app-backend]",
      "description": "New container app-backend detected",
      "old_value_hash": null,
      "new_value_hash": "sha256:jkl012...",
      "severity": "info"
    }
  ]
}
```

### 6.4 Manifeste des changements (`changes/manifest.json`)

```json
{
  "schema_version": "1.0",
  "manifest_version": "1.0",
  "server_uuid": "a1b2c3d4-e5f6-7890-abcd-ef1234567890",
  "generated_at": "2026-07-03T16:34:35Z",
  "total_entries": 87,
  "retention_entries": 200,
  "entries": [
    {
      "change_id": "c3d4e5f6-a7b8-90ab-cdef-012345678901",
      "timestamp_utc": "2026-07-03T16:34:30Z",
      "collector": "docker",
      "severity": "warning",
      "total_changes": 5,
      "file": "2026-07-03T16:34:30Z.json"
    },
    {
      "change_id": "b2c3d4e5-f6a7-89bc-def0-123456789012",
      "timestamp_utc": "2026-07-02T06:00:15Z",
      "collector": "apt",
      "severity": "info",
      "total_changes": 12,
      "file": "2026-07-02T06:00:15Z.json"
    }
  ],
  "severity_summary": {
    "critical": 2,
    "warning": 31,
    "info": 54
  }
}
```

### 6.5 Règles de comparaison

La comparaison est **structurelle et récursive** :

1. **Clé présente dans ancien ET nouveau** → comparer les valeurs par type :
   - `str`, `int`, `float`, `bool` : comparaison directe
   - `list` : comparaison index par index ; si longueur différente →ajout/suppression
   - `dict` : récursion sur les clés communes
2. **Clé présente uniquement dans ancien** → `removed`
3. **Clé présente uniquement dans nouveau** → `added`
4. **Pour les dicts imbriqués**, le `path` est construit en JSONPath notation : `content.processes[0].pid`
5. **Hash avant comparison** : les valeurs complexes (dict, list) sont hashées SHA256 avant comparaison pour éviter de stocker des snapshots complets en mémoire

### 6.6 Exemples JSON complets de changements

---

#### Exemple 1 : Changement de service systemd (`changes/2026-07-03T08:00:00Z.json`)

```json
{
  "schema_version": "1.0",
  "change_id": "d4e5f6a7-b8c9-01de-f2a3-b4c5d6e7f8a9",
  "run_id": "e5f6a7b8-c9d0-e1f2-a3b4-c5d6e7f8a9b0",
  "timestamp_utc": "2026-07-03T08:00:00Z",
  "collector": "systemd_services",
  "severity": "critical",
  "summary": "3 services changed state (1 failed, 1 stopped, 1 started)",
  "total_changes": 3,
  "changes": [
    {
      "type": "modified",
      "path": "content.services[name=nginx.service].state",
      "description": "Service nginx changed from active to failed",
      "old_value": "active (running)",
      "new_value": "failed",
      "old_value_hash": "sha256:state001...",
      "new_value_hash": "sha256:state002...",
      "severity": "critical"
    },
    {
      "type": "removed",
      "path": "content.services[name=cron.service]",
      "description": "Service cron no longer in active services list",
      "old_value_hash": "sha256:cron001...",
      "new_value_hash": null,
      "severity": "warning"
    },
    {
      "type": "added",
      "path": "content.services[name=backup.timer]",
      "description": "New timer backup.timer detected",
      "old_value_hash": null,
      "new_value_hash": "sha256:timer001...",
      "severity": "info"
    }
  ]
}
```

---

#### Exemple 2 : Changement réseau (`changes/2026-07-02T12:00:00Z.json`)

```json
{
  "schema_version": "1.0",
  "change_id": "e5f6a7b8-c9d0-e1f2-a3b4-c5d6e7f8a9b0",
  "run_id": "f6a7b8c9-d0e1-f2a3-b4c5-d6e7f8a9b0c1",
  "timestamp_utc": "2026-07-02T12:00:00Z",
  "collector": "network",
  "severity": "warning",
  "summary": "2 listening ports changed",
  "total_changes": 2,
  "changes": [
    {
      "type": "added",
      "path": "content.listening_ports[protocol=tcp,port=8443]",
      "description": "New TCP port 8443 listening on 0.0.0.0 (process: nginx)",
      "old_value_hash": null,
      "new_value_hash": "sha256:port8443...",
      "severity": "warning"
    },
    {
      "type": "modified",
      "path": "content.established_connections",
      "description": "Number of established connections changed",
      "old_value": 8,
      "new_value": 14,
      "old_value_hash": "sha256:conn008...",
      "new_value_hash": "sha256:conn014...",
      "severity": "info"
    }
  ]
}
```

---

#### Exemple 3 : Changement utilisateurs (`changes/2026-07-01T06:00:00Z.json`)

```json
{
  "schema_version": "1.0",
  "change_id": "f6a7b8c9-d0e1-f2a3-b4c5-d6e7f8a9b0c1",
  "run_id": "a7b8c9d0-e1f2-a3b4-c5d6-e7f8a9b0c1d2",
  "timestamp_utc": "2026-07-01T06:00:00Z",
  "collector": "users",
  "severity": "warning",
  "summary": "1 user account added",
  "total_changes": 1,
  "changes": [
    {
      "type": "added",
      "path": "content.users[username=deploy]",
      "description": "New user account 'deploy' added (uid=1001, groups=sudo,docker)",
      "old_value_hash": null,
      "new_value_hash": "sha256:userdeploy...",
      "severity": "warning"
    }
  ]
}
```

---

## 7. Sécurité

### 7.1 Arborescence FHS et permissions

Tous les répertoires et leurs permissions sont créés par `install.sh` et ne doivent jamais être modifiés manuellement :

| Répertoire | Propriétaire | Permissions | Contenu |
|---|---|---|---|
| `/opt/aicollector/` | `root:root` | `755` | Code de l'application — lecture seule pour le service |
| `/etc/aicollector/` | `root:aicollector` | `750` | `config.yaml` — lecture seule pour le service |
| `/var/lib/aicollector/` | `aicollector:aicollector` | `750` | `knowledge/`, `history/`, `changes/`, `manifest.json` |
| `/var/cache/aicollector/` | `aicollector:aicollector` | `750` | `cache/` — régénérable, non critique |
| `/run/aicollector/` | `aicollector:aicollector` | `755` | `aicollector.lock` — recréé après reboot via tmpfiles.d |
| `/var/log/aicollector/` | `aicollector:aicollector` | `750` | `aicollector.log` + rotation logrotate |

**Utilisateur système dédié :** Le service s'exécute sous l'utilisateur `aicollector` créé par `install.sh` (`useradd --system --no-create-home --shell /usr/sbin/nologin aicollector`). Cet utilisateur n'a **aucun privilège sudo** — il ne peut que lire les informations système autorisées par la whitelist de `SystemAdapter`. L'utilisateur doit appartenir au groupe `docker` si le collecteur Docker est utilisé.

**Règle absolue :** Le code de `/opt/aicollector/` ne doit jamais être modifié par l'utilisateur `aicollector`. Toute mise à jour du code se fait via une réinstallation de `install.sh` (qui remplace le contenu de `/opt/aicollector/`).

### 7.2 Commandes autorisées (whitelist)

La whitelist est définie dans `SystemAdapter.ALLOWED_COMMANDS`. Toute commande ne figurant pas dans cette liste provoque une erreur `ForbiddenCommandError` bloquante :

```
systemctl, docker, ip, journalctl, ss, apt, dpkg, ufw, iptables,
smartctl, auditctl, crontab, openssl, lsblk, df, sensors, nproc,
hostname, uname, ls, cat, grep, awk, cut, sort, uniq, wc,
find, stat, id, whoami, uptime, free, mount, ps, netstat
```

**Règle absolue :** `shell=True` est **strictement interdit**. Les commandes sont passées sous forme de liste d'arguments : `["systemctl", "status", "nginx"]`.

### 7.3 Fichiers et chemins interdits

Les chemins suivants sont **systématiquement exclus** de toute lecture ou sérialisation :

| Chemin / Pattern | Raison |
|---|---|
| `/etc/shadow` | Hash de mots de passe |
| `/root/.ssh/` | Clés SSH privées |
| `/home/*/.ssh/` | Clés SSH privées utilisateur |
| `/var/lib/docker/*/config.v2.json` | Config Docker contenant auths |
| `*.key`, `*.pem`, `*.crt` (dans résultats) | Clés et certificats privés |
| `/proc/sys/crypto/` | Clés de chiffrement noyau |
| Variables d'environnement `*_PASSWORD`, `*_SECRET`, `*_KEY`, `*_TOKEN` | Secrets applicatifs |

### 7.4 Secrets à exclure (sanitizer)

Le module `sanitizer.py` détecte et remplace les patterns de secrets dans les données avant sérialisation :

| Type | Pattern | Remplacement |
|---|---|---|
| Mot de passe | `(password\|passwd\|pwd)["\s:=]+([^"'\s]+)` | `***REDACTED***` |
| Clé API | `(api[_-]?key)["\s:=]+([^"'\s]+)` | `***REDACTED***` |
| Token Bearer | `(bearer[_-]?token)["\s:=]+([^"'\s]+)` | `***REDACTED***` |
| Clé SSH / TLS | `-----BEGIN (RSA\|EC\|DSA\|OPENSSH) PRIVATE KEY-----` | `***REDACTED***` |
| AWS Access Key | `AKIA[0-9A-Z]{16}` | `***REDACTED***` |
| Carte bancaire | `\d{4}[\s-]?\d{4}[\s-]?\d{4}[\s-]?\d{4}` | `***REDACTED***` |
| Variable d'environnement secrète | `(SECRET\|PASSWORD\|API_KEY)["\s]?=.*` | `***REDACTED***` |

### 7.5 Protections anti-fuite

1. **Aucune transmission réseau** : le projet ne fait aucune requête HTTP en V1.0. Les données sont stockées localement uniquement.
2. **Aucune persistence de secrets** : les secrets détectés sont remplacés avant sérialisation.
3. **Audit de log** : chaque détection de secret génère un événement `security.secret_redacted` dans les logs.
4. **Base de connaissances non chiffrée** : en V1.0, les JSON dans `knowledge/` sont en texte clair. Si des données sensibles y apparaissent (un chemin, un nom de container), le sanitizer les nettoie automatiquement.
5. **Absence de credentials dans les métadonnées** : `server_uuid` est un UUID généré aléatoirement, pas un hostname computable comme identifiant. Il n'y a pas de correlation directe avec le hostname.

### 7.6 Considérations root

- Les collecteurs marqués `requires_root=True` peuvent échouer silencieusement si lancés sans droits root (comportement configurable via `config.yaml : collectors.root_required_behavior` : `skip | warn | fail`)
- Le collecteur **ne tente jamais** d'utiliser `sudo` en interne — c'est la responsabilité de l'opérateur de configurer les permissions correctement
- Les collecteurs root-requiring doivent documenter pourquoi ils ont besoin de droits root

---

## 8. Performances

### 8.1 Objectifs cibles

| Métrique | Cible | Seuil d'alerte |
|---|---|---|
| RAM totale utilisée | < 100 Mo | > 150 Mo |
| CPU pendant collecte | < 5 % | > 10 % |
| Temps d'exécution complet | < 30 s | > 60 s |
| Temps par collecteur | < timeout configuré | timeout × 0.8 |
| Taille fichier JSON (un collecteur) | < 1 Mo | > 5 Mo |
| I/O disque (un run) | < 50 Mo (écritures) | > 100 Mo |
| Cache mémoire (intra-run) | < 10 Mo | > 20 Mo |

### 8.2 Optimisation des appels système

Le `SystemAdapter` implémente un **cache intra-run** pour éviter les appels répétés :

```
_cache: dict[str, Any]
  clé = "cmd:systemctl:list-units:--all"  (tuple normalisé → string)
  valeur = (result, timestamp)
  TTL = durée du run (vidage complet entre chaque run)
```

Les lectures multiples du même fichier `/proc/` (ex: `/proc/stat` pour `cpu.py` et `system.py`)ne sont effectuées qu'une seule fois par run.

### 8.3 Optimisation du pipeline

| Optimisation | Détail |
|---|---|
| Exécution séquentielle des collecteurs | Évite la surcharge CPU sur des collecteurs I/O-bound ; simple et prévisible |
| Validation Pydantic lazy | La validation n'est faite qu'en phase NORMALIZE, pas en phase COLLECT |
| Bufferisation des logs | Les entrées de log sont bufferisées en mémoire et flushées à la fin du run |
| Purge FIFO différée | La purge de `history/` et `changes/` n'est faite qu'après l'écriture du dernier fichier, pas en temps réel |

### 8.4 Optimisation disque

| Pratique | Détail |
|---|---|
| Timestamps ISO8601 compact | `2026-07-03T16:34:00Z` (pas de format lisible humain, 1 seul format) |
| Compression optionnelle | `config.yaml` peut activer la compression gzip des JSON dans `knowledge/` |
| Rotation des logs | `TimedRotatingFileHandler` quotidien, 30 jours conservés |
| Rotation de l'historique | Copies numériques `0001.json...0050.json` (pas de horodatage dans le nom — facilite la purge FIFO) |

### 8.5 Taille maximale des fichiers

Chaque fichier JSON dans `knowledge/` est validé à l'écriture :

- Taille max : **10 Mo** par fichier (levée `KnowledgeWriteError` si dépassé)
- Nombre max de clés racines : **500** par objet de premier niveau
- Nombre max d'éléments dans une liste : **10 000**

Ces limites sont configurables via `config.yaml`.

---

## 9. Gestion des erreurs

### 9.1 Classification des erreurs

| Catégorie | Définition | Comportement |
|---|---|---|
| **Bloquante** | Empêche le run de se terminer correctement | Log FATAL, exit code != 0, pas d'écriture dans `knowledge/` — correspond à `is_blocking=True` |
| **Non bloquante** | Un collecteur échoue mais les autres continuent | Log ERROR pour le collecteur, `CollectorError` dans le résultat, run continue — correspond à `is_blocking=False` |
| **Warning** | Anomalie détectée mais données disponibles | Log WARNING, données incluent `inconsistencies_detected` |
| **Info** | Événement normal notable | Log INFO |

### 9.2 Hiérarchie des erreurs

```
AICollectorError (base, exit_code=1, is_blocking=False)
├── CollectorError (exit_code=2, is_blocking=False)
│   ├── CollectorNotFoundError (exit_code=2, is_blocking=False)
│   ├── CollectorTimeoutError (exit_code=3, is_blocking=False)
│   └── CollectorPermissionError (exit_code=3, is_blocking=False)
├── SystemAdapterError (exit_code=4, is_blocking=False)
│   ├── ForbiddenCommandError (exit_code=4, is_blocking=True)  ← BLOQUANT
│   ├── CommandExecutionError (exit_code=4, is_blocking=False)  ← NON BLOQUANT (Décision #5)
│   └── ProcFileReadError (exit_code=5, is_blocking=False)
├── ConfigError (exit_code=10, is_blocking=True)  ← BLOQUANT
│   ├── ConfigFileNotFoundError (exit_code=10, is_blocking=True)
│   └── ConfigValidationError (exit_code=10, is_blocking=True)
├── PipelineError (exit_code=20, is_blocking=True)  ← BLOQUANT
│   ├── PhaseError (exit_code=20, is_blocking=True)
│   └── RunInterruptedError (exit_code=20, is_blocking=True)
├── LockfileError (exit_code=30, is_blocking=True)  ← BLOQUANT
├── SchemaValidationError (exit_code=40, is_blocking=False)
├── KnowledgeStoreError (exit_code=50, is_blocking=False)
│   ├── KnowledgeWriteError (exit_code=51, is_blocking=False)
│   └── HistoryReadError (exit_code=52, is_blocking=False)
└── EventBusError (exit_code=60, is_blocking=False)
```

### 9.3 Table des codes d'erreur

| Code | Classe | Type | `is_blocking` | Run interrompu | Description |
|---|---|---|---|---|---|
| 1 | `AICollectorError` | Base | `False` | Non | Erreur générique non classifiée |
| 2 | `CollectorError` | Non bloquant | `False` | Non | Erreur collecteur générique |
| 3 | `CollectorTimeoutError` | Non bloquant | `False` | Non | Timeout collecteur dépassé |
| 4 | `ForbiddenCommandError` | **Bloquant** | `True` | Oui | Commande hors whitelist |
| 5 | `ProcFileReadError` | Non bloquant | `False` | Non | Fichier `/proc/` illisible |
| 10 | `ConfigFileNotFoundError` | **Bloquant** | `True` | Oui | Fichier config absent |
| 11 | `ConfigValidationError` | **Bloquant** | `True` | Oui | Config YAML invalide |
| 20 | `PipelineError` | **Bloquant** | `True` | Oui | Erreur dans le pipeline |
| 30 | `LockfileError` | **Bloquant** | `True` | Oui | Run déjà en cours |
| 40 | `SchemaValidationError` | Non bloquant | `False` | Non | JSON ne valide pas le schéma |
| 50 | `KnowledgeWriteError` | Non bloquant | `False` | Non | Échec écriture knowledge/ |
| 51 | `HistoryReadError` | Non bloquant | `False` | Non | Historique illisible |
| 60 | `EventBusError` | Non bloquant | `False` | Non | Erreur bus d'événements |

> **Note sur `is_blocking` (Décision #5) :** Cette colonne explicite le champ public `is_blocking` introduit dans toutes les exceptions. Le pipeline interroge `exc.is_blocking` pour décider si le run doit être interrompu. Les exceptions propagées chainent leur cause via `raise X from e`.

### 9.4 Comportements attendus par erreur

| Erreur | Comportement |
|---|---|
| `CollectorTimeoutError` | Le collecteur est marqué `failed`, son JSON n'est pas écrit, un événement `collector.failed` est émis, le pipeline continue |
| `ForbiddenCommandError` | Log FATAL, **run interrompu immédiatement**, pas d'écriture, exit code 4 |
| `ConfigValidationError` | Log FATAL avec détails du champ invalide, exit code 10 |
| `LockfileError` | Log FATAL, exit code 30, suggère de vérifier les processus en cours |
| `CommandExecutionError` (Décision #5) | Erreur d'exécution d'une commande collecteur individuelle — **`is_blocking=False`**. L'erreur est capturée dans `CollectorResult.errors`, le pipeline continue avec une collecte partielle. |
| `SchemaValidationError` | Log WARNING, le JSON brut est écrit avec `confidence_score=0.0`, l'agent IA sait que les données ne sont pas validées |
| `KnowledgeWriteError` | Log ERROR, retry une fois après 1 seconde ; si échec, warning non bloquant |
| `PermissionError` (sans root) | Log WARNING, collecteur marqué `requires_root` → désactivé pour ce run |

### 9.5 Journalisation

- **Tous les événements** (y compris les erreurs) sont journalisés dans `logs/aicollector-YYYY-MM-DD.log`
- Les erreurs FATAL incluent le `run_id` et la stack trace complète
- Les erreurs non bloquantes incluent le `collector_name` et le `error_type`
- Les statistiques de run (collecteurs actifs, échoués, changements) sont logguées à chaque `run.finished`
- **Aucune information sensibles n'est journalisée** (les secrets détectés par le sanitizer apparaissent comme `***REDACTED***`)

### 9.6 Récupération automatique

| Situation | Récupération |
|---|---|
| Fichier `knowledge/<collecteur>.json` manquant | Le collecteur est ré-exécuté et le fichier est recréé |
| Lockfile orphelin (processus mort) | Auto-nettoyage au démarrage si le PID n'est plus vivant |
| Espace disque insuffisant | Log FATAL, run interrompu avec `KnowledgeStoreError` |
| Fichier `config.yaml` modifié entre deux runs | Re-chargé à chaque run, pas de cache |

---

## 10. Extensibilité

### 10.1 Ajouter un nouveau collecteur

**Règle absolue :** Aucun composant du pipeline (`pipeline.py`, `registry.py`, etc.) ne doit être modifié. Seuls les points d'extension prévus (`schemas.py`, exporters, etc.) peuvent être étendus. Le nouveau collecteur vit dans `collectors/`.

**Arborescence cible :** En mode production, les collecteurs vivent dans `/opt/aicollector/collectors/`. En mode dev, `AICOLLECTOR_ROOT` redirige ce chemin vers le dossier de travail courant.

**Étapes :**

1. Créer `/opt/aicollector/collectors/mon_collecteur.py` (prod) ou `collectors/mon_collecteur.py` (dev)
2. Importer `BaseCollector` depuis `core.base_collector`
3. Hériter de `BaseCollector`
4. Ajouter le décorateur `@register_collector("mon_collecteur")`
5. Implémenter `collect(self, system: SystemAdapter) -> CollectorResult`
6. Créer le schéma Pydantic correspondant dans `core/schemas.py` en tant que point d'extension prévu, via le décorateur `@register_collector_schema` (section séparée — le registre dynamique met à jour la validation automatiquement, aucun ajout manuel dans `CollectorSchemaUnion` n'est nécessaire).
   Pour référence, voici le schéma du collecteur `apt` (collecteur de paquets APT) :
   ```python
   class APTPackage(BaseModel):
       name: str
       architecture: str
       version: str
       installed_version: str
       size_bytes: int
       description: str
       status: Literal["installed", "upgradable", "not-installed"]

class APTContent(BaseModel):
       distribution: str
             total_packages: int
       installed_packages: int
       upgradable_packages: int
       packages: list[APTPackage]
       upgradable_packages_list: list[APTPackage]

@register_collector_schema("apt")
   class APTCollectorSchema(CollectorSchema):
       content: APTContent
   ```
7. Tester avec `python collector.py --collector mon_collecteur --dry-run`

> **Données produites :** `/var/lib/aicollector/knowledge/<nom>.json` (dernier snapshot)
> **Historique :** `/var/lib/aicollector/history/<nom>/` (rotation FIFO)
> **Changements :** `/var/lib/aicollector/changes/<timestamp>.json`

**Structure minimale :**

```
/opt/aicollector/collectors/       # prod  (ou ./collectors/ en mode dev)
├── __init__.py
├── cpu.py
├── memory.py
└── mon_collecteur.py             # ← Nouveau collecteur
```

**Exemple minimal complet :**

```python
# collectors/example.py
"""
Collecteur exemple — monitor_disk_usage.
Collecte l'utilisation des points de montage via `df`.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from core.base_collector import BaseCollector, CollectorResult, CollectorCapabilities, Severity
from core.system_adapter import SystemAdapter
from core.registry import register_collector


@register_collector("disk_usage")
class DiskUsageCollector(BaseCollector):
    name = "disk_usage"
    schema_version = "1.0"
    collector_version = "1.0.0"
    requires_root = False
    timeout_seconds = 20

    def collect(self, system: SystemAdapter) -> CollectorResult:
        """Collecte l'utilisation des systèmes de fichiers."""
        import time
        start = time.monotonic()

        errors: list[dict] = []
        data: dict[str, Any] = {"mounts": []}  # interne : brut, avant parsing

        result = system.run_command("df", ["-h", "--output=source,target,size,used,avail,pcent,fstype"])
        if result.returncode != 0:
            errors.append({"type": "command_error", "detail": result.stderr})
        else:
            lines = result.stdout.strip().split("\n")[1:]  # skip header
            for line in lines:
                parts = [p for p in line.split() if p]
                if len(parts) >= 7:
                    try:
                        used_pct = int(parts[6].rstrip("%"))
                        data["mounts"].append({
                            "device": parts[0],
                            "mountpoint": parts[1],
                            "size": parts[2],
                            "used": parts[3],
                            "available": parts[4],
                            "usage_percent": used_pct,
                            "fstype": parts[7] if len(parts) > 7 else "unknown",
                        })
                    except (ValueError, IndexError):
                        errors.append({"type": "parse_error", "line": line})

        return CollectorResult(
            data=data,
            errors=errors,
            execution_time_ms=(time.monotonic() - start) * 1000,
            raw_output=result.stdout if result.returncode == 0 else None,
        )

    def capabilities(self) -> CollectorCapabilities:
        return CollectorCapabilities(
            supported_platforms=["linux"],
            min_confidence=0.95,
            known_inconsistencies=[
                "df output may vary across distributions",
                "tmpfs and devtmpfs mounts are included",
            ],
        )

    def classify_change(
        self, path: str, old_value: Any, new_value: Any
    ) -> Severity:
        """Alarm on high disk usage (> 90%)."""
        if "usage_percent" in path:
            try:
                new_pct = int(new_value) if isinstance(new_value, int) else int(str(new_value).rstrip("%"))
                if new_pct >= 95:
                    return Severity.CRITICAL
                elif new_pct >= 90:
                    return Severity.WARNING
            except (ValueError, TypeError):
                pass
        return Severity.INFO
```

### 10.2 Ajouter un export / sink

> **Note :** Cette section décrit l'architecture prévue pour les exporters. Les exporters ne font pas partie du périmètre V1.0 et sont planifiés en V1.1 (voir Roadmap section 12).

>L'extensibilité vers de nouveaux formats d'export (CSV, PostgreSQL, webhook HTTP) se fait via le pattern **Strategy** :

```
/opt/aicollector/core/                # prod  (ou ./core/ en mode dev)
  ├── exporters/
  │   ├── __init__.py
  │   ├── base_exporter.py    # ABC Exporter avec méthode export(knowledge_dir)
  │   ├── json_exporter.py    # Export JSON (défaut)
  │   ├── csv_exporter.py     # Export CSV
  │   └── webhook_exporter.py # Export HTTP POST (V2+)
  └── pipeline.py             # Appelle exporter.export() après KNOWLEDGE BASE
```

L'exporteur est sélectionnable via `config.yaml : export.format`. Les exporters consomment les données depuis `/var/lib/aicollector/knowledge/` (ou `$AICOLLECTOR_ROOT/knowledge/` en mode dev).

### 10.3 Ajouter une source de données

Une source de données alternative (ex: SNMP, IPMI, API REST d'un service tiers) s'ajoute comme un collecteur spécial qui n'utilise pas `SystemAdapter` mais une bibliothèque dédiée (`pysnmp`, `pyghmi`, `requests`) — toujours en lecture seule, toujours avec whitelist de requêtes.

> **Exception officielle au principe du SystemAdapter :** les collecteurs réseau/distants (`requests`, `pysnmp`, etc.) constituent une exception documentée au principe du SystemAdapter, car ils n'interagissent pas avec le système local mais avec des ressources distantes. `SystemAdapter` reste l'unique abstraction pour les appels système locaux (`systemctl`, `docker`, `ip`, `journalctl`, etc.). En V2, un `NetworkAdapter` pourra mutualiser les paramètres HTTP (timeout, retry, TLS) — ce n'est pas une exigence V1.

### 10.4 Ajouter un schéma JSON (nouveau format de collecteur)

Pour ajouter un nouveau schéma dans `schemas.py` :

```python
# core/schemas.py — enregistrement dynamique des schémas de collecteurs

from __future__ import annotations
from typing import Annotated, Any, Union
from pydantic import BaseModel, Field

# Registre global : map source name -> schema class
_COLLECTOR_SCHEMA_REGISTRY: dict[str, type[BaseModel]] = {}


def register_collector_schema(name: str):
    """Décorateur pour enregistrer dynamiquement un schéma de collecteur.
    
    Chaque collecteur appelle ce décorateur dans schemas.py pour s'enregistrer.
    Le registre est utilisé par le validateur pour valider les JSON sans qu'il
    soit nécessaire de modifier manuellement une Union.
    """
    def decorator(schema_class: type[BaseModel]) -> type[BaseModel]:
        _COLLECTOR_SCHEMA_REGISTRY[name] = schema_class
        return schema_class
    return decorator


def validate_knowledge_json(data: dict[str, Any]) -> BaseModel:
    """Valide un JSON de knowledge : retourne l'instance du schéma correspondant.
    
    Utilisé par knowledge_store.py pour valider les JSON avant écriture.
    """
    source = data.get("source")
    if source in _COLLECTOR_SCHEMA_REGISTRY:
        return _COLLECTOR_SCHEMA_REGISTRY[source].model_validate(data)
    # Fallback : dict non validé (collecteur inconnu, lecture seule)
    return data  # type: ignore[return-value]


# --- Schémas enregistrés ---

@register_collector_schema("cpu")
class CPUCollectorSchema(BaseModel):
    source: Literal["cpu"] = "cpu"
    # ...

@register_collector_schema("network")
class NetworkCollectorSchema(BaseModel):
    source: Literal["network"] = "network"
    # ...

# Tout nouveau collecteur s'enregistre ici via le décorateur @register_collector_schema.
# Aucune modification manuelle de CollectorSchemaUnion n'est nécessaire.
```

### 10.5 Ajouter un type d'événement EventBus

Pour ajouter un nouveau type d'événement (ex: `export.started`) :

1. Définir la constante dans `core/event_bus.py` : `EXPORT_STARTED = "export.started"`
2. Émettre avec `event_bus.emit(Event(EXPORT_STARTED, {...}))` dans le module concerné
3. Le logger s'abonne automatiquement (via le pattern `*` wildcard)
4. Pas besoin de modifier `core/event_bus.py` pour ajouter des handlers

---

## 11. Standards de développement

### 11.1 Conventions de nommage

| Élément | Convention | Exemple |
|---|---|---|
| Fichiers Python | `snake_case.py` | `system_adapter.py` |
| Classes | `PascalCase` | `SystemAdapter`, `CollectorResult` |
| Fonctions / méthodes | `snake_case` | `run_command`, `compute_json_hash` |
| Constantes module | `SCREAMING_SNAKE_CASE` | `ALLOWED_COMMANDS` |
| Variables locales | `snake_case` | `execution_time_ms` |
| Attributs de dataclass | `snake_case` | `execution_time_ms` |
| Enums | `SCREAMING_SNAKE_CASE` (valeurs lowercase) | `Severity.INFO` |
| Attributs de classe ABC | `snake_case` | `timeout_seconds` |
| Fichiers JSON de sortie | `kebab-case.json` | `systemd-services.json` |
| Répertoires | `kebab-case/` | `knowledge/`, `changes/` |

### 11.2 Structure du code (style)

```python
"""
Module docstring : une phrase décrivant le rôle du module.
Paragraphes optionnels pour les détails d'implémentation importants.
"""
from __future__ import annotations  # Pour les annotations forward references

# --- standard library ---
import sys
from pathlib import Path
from typing import ClassVar

# --- third party ---
from pydantic import BaseModel, Field, ConfigDict
import yaml

# --- local application ---
from core.base_collector import BaseCollector
from core.system_adapter import SystemAdapter
from core.registry import register_collector


class MonCollecteur(BaseCollector):
    """
    Docstring de classe décrivant le collecteur, ses données collectées
    et ses limitations.
    """

    name: ClassVar[str] = "mon_collecteur"
    schema_version: ClassVar[str] = "1.0"
    collector_version: ClassVar[str] = "1.0.0"
    requires_root: ClassVar[bool] = False
    timeout_seconds: ClassVar[int] = 20

    def collect(self, system: SystemAdapter) -> CollectorResult:
        """
        Collecte les données via le SystemAdapter.

        Args:
            system: Instance de SystemAdapter injectée par le pipeline.

        Returns:
            CollectorResult contenant les données brutes.

        Raises:
            CollectorTimeoutError: Si le timeout est dépassé.
        """
        ...  # Corps de la méthode

    def classify_change(
        self, path: str, old_value: object, new_value: object
    ) -> Severity:
        """Classifier la sévérité d'un changement."""
        ...
```

### 11.3 Typage Python

- **Toutes les fonctions et méthodes** doivent avoir des type hints complets (y compris le type de retour `-> None` si applicable)
- **`Any` interdit en sortie publique** : Any est interdit dans les signatures de fonctions/méthodes publiques et dans les types de retour. Il est toléré uniquement en interne pour du contenu brut non encore validé (ex: sortie brute d'une commande système avant parsing), et doit alors être immédiatement typé/validé via un schéma Pydantic avant d'être exposé publiquement.
- Utiliser `typing.IO`, `typing.TextIO` pour les fichiers
- Pour les dataclasses : `slots=True` sur toutes les dataclasses, `frozen=True` sur les structures transitant entre les phases (événements, résultats)
- Import depuis `typing` uniquement pour les types complexes : `from typing import ClassVar, Any`

### 11.4 Documentation

| Élément | Exigence |
|---|---|
| Docstring de module | Obligatoire (1 phrase minimum) |
| Docstring de classe | Obligatoire (incluant les limitations) |
| Docstring de méthode publique | Obligatoire (Args, Returns, Raises) |
| Commentaire `# --- sections ---` | Obligatoire entre les sections d'un fichier |
| `__all__` | Recommandé sur chaque module pour exposer l'API publique |

### 11.5 Tests

**Couverture minimale V1.0 :**

- Tests unitaires pour chaque module de `core/`
- Tests d'intégration pour le pipeline complet (avec mock du système)
- Tests de collecteurs individuels (avec mock de `SystemAdapter`)
- Structure : `tests/` parallèle à `core/` et `collectors/`

```
tests/
├── unit/
│   ├── test_system_adapter.py
│   ├── test_event_bus.py
│   ├── test_registry.py
│   ├── test_hashing.py
│   ├── test_sanitizer.py
│   ├── test_diff_engine.py
│   └── test_config_loader.py
├── integration/
│   ├── test_pipeline.py
│   └── test_knowledge_store.py
└── collectors/
    ├── test_cpu.py
    ├── test_docker.py
    └── test_network.py
```

**Outil de test :** `pytest` (dépendances optionnelles).

### 11.6 Organisation des modules

```
# core/ — Cœur du projet, NE PAS modifier par un plugin
# collectors/ — Plugins auto-découverts, AUCUN import depuis core/ qui ne soit pas base_collector
# tests/ — Tests unitaires et d'intégration
# docs/ — Documentation utilisateur (RTFM)
# tools/ — Scripts utilitaires (migration, export, diagnostique)
```

---

## 12. Roadmap

### V0.1 — Fondation technique ⏱ Objectif : 2 semaines

**Fonctionnalités :**
- [ ] Architecture pipeline 4 phases (COLLECT → NORMALIZE → COMPARE → KNOWLEDGE BASE)
- [ ] Registre dynamique de collecteurs avec `@register_collector`
- [ ] SystemAdapter avec whitelist de commandes et cache intra-run
- [ ] EventBus synchrone in-process
- [ ] Dataclasses de transit (`CollectorResult`, `PipelineStats`, `Event`)
- [x] Fichier `config.yaml` avec validation Pydantic
- [ ] Lockfile avec détection de PID mort
- [ ] Structure de répertoires (`knowledge/`, `changes/`, `history/`, `logs/`)

**Objectifs de validation :**
- `python collector.py --run` s'exécute sans erreur sur un serveur Ubuntu vierge
- Les 4 collecteurs de base (system, cpu, ram, storage) produisent des JSON valides
- Le lockfile empêche les runs simultanés
- Aucun secret n'est sérialisé dans les JSON

**Critères de validation :**
- ✅ 0 erreur FATAL sur un run standard
- ✅ Tous les JSON passent la validation Pydantic
- ✅ Le global_hash change quand les données changent
- ✅ Le manifest.json est cohérent avec les fichiers dans `knowledge/`

---

### V0.2 — Collecteurs noyau et détection de changement ⏱ Objectif : 2 semaines

**Fonctionnalités :**
- [x] 8 collecteurs noyau : network, docker, systemd_services, firewall, auditd, apt, users, cron
- [ ] `diff_engine.py` fonctionnel avec comparison récursive
- [ ] Écriture de `changes/<timestamp>.json`
- [ ] Mise à jour de `changes/manifest.json`
- [ ] Rotation de `history/` par version (FIFO)
- [ ] Purge FIFO de `changes/`
- [ ] `self_diagnostic.py` au démarrage

**Objectifs de validation :**
- Un changement dans Docker (nouveau container) est détecté et persisté
- Les changements critiques (service failed) ont `severity: critical`
- L'historique conserve les 50 dernières versions
- Le manifeste de changements est à jour après 10 runs

**Critères de validation :**
- ✅ Changement Docker détecté entre run_1 et run_2
- ✅ `changes/` contient un fichier par changement détecté
- ✅ `changes/manifest.json` a le bon `total_entries`
- ✅ Purge FIFO appliquée quand `changes/` dépasse 200 entrées

---

### V0.3 — Validation et hardening ⏱ Objectif : 2 semaines

**Fonctionnalités :**
- [ ] 7 collecteurs complémentaires : smart, timers, ssl_certificates, syslogs + 3 à définir
- [ ] `sanitizer.py` avec regex complètes et événements `security.secret_redacted`
- [ ] Logging JSON structuré dans `logs/`
- [ ] Rotation quotidienne des logs
- [ ] Tests unitaires sur `core/` (cible : 80 % de couverture)
- [ ] Auto-diagnostic détaillé au démarrage
- [ ] Documentation developer's guide

**Objectifs de validation :**
- Le sanitizer remplace tous les types de secrets de la liste
- Les tests unitaires passent à 80 % de couverture
- Le logger produit du JSON structuré (NDJSON) lisible
- L'auto-diagnostic ne passe pas si Python < 3.12

**Critères de validation :**
- ✅ `pytest tests/ --cov=aicollector --cov-report=term-missing` ≥ 80 %
- ✅ Faux positifs du sanitizer < 1 % (pas de remplacement de texte légitime)
- ✅ Logs NDJSON parseables par `jq`

---

### V1.0 — Release candidate ⏱ Objectif : 3 semaines

**Fonctionnalités :**
- [ ] Tous les 15 collecteurs opérationnels
- [ ] Documentation complète (SPEC.md + developer's guide + man page)
- [ ] Intégration cron (fréquence par défaut : toutes les 2h — `0 */2 * * *`, modifiable par l'utilisateur via `config.yaml`)
- [ ] Option systemd timer (template fourni, désactivé par défaut)
- [ ] Script d'installation `install.sh` idempotent : crée l'utilisateur système `aicollector`, l'arborescence FHS, les permissions, les fichiers cron/systemd
- [ ] Mode dev via `AICOLLECTOR_ROOT` ou `--dev-mode` (chemins redirigés vers le dossier courant)
- [ ] Tests d'intégration pipeline complet
- [ ] Validation de performance (< 100 Mo RAM, < 30 s, < 5 % CPU)
- [ ] Gestion propre des erreurs (tous les cas de la table des codes)
- [ ] Publication sur GitHub (repo public)

**Objectifs de validation :**
- Run complet sur un serveur de production (150+ containers Docker)
- Temps d'exécution < 30 s (serveur standard, pas de rush)
- RAM < 100 Mo pendant l'exécution
- Aucune fuite mémoire entre deux runs
- Toutes les exceptions levées sont documentées et testées

**Critères de validation :**
- ✅ `python collector.py --run` → 0 FATAL sur 10 runs consécutifs
- ✅ RAM peak < 100 Mo (mesuré via `/proc/self/status`)
- ✅ Tous les collecteurs génèrent des JSON valides Pydantic
- ✅ Changements détectés sur 3 runs consécutifs (données suffisamment variées)
- ✅ Documentation complète accessible

---

### V1.1 — extensibilité (post-release)

**Fonctionnalités :**
- Webhook exporter (HTTP POST vers une URL configurable)
- CSV exporter
- SSH collector (collecte sur hôte distant)
- Dashboard web (lecture de la base de connaissances)
- Intégration LLM (déclenchement automatique d'analyse IA sur changements critiques)

---

## 13. Analyse critique

> Cette section est un exercice d'honnêteté intellectuelle. Les points ci-dessous sont des risques et faiblesses réelles, pas des rêves maxima. Chaque point liste aussi une solution proposée, argumentée.

---

### 13.1 Problèmes identifiés

_Cette sous-section recense les bugs, incohérences et risques immédiatement identifiables dans la conception actuelle. Ils doivent être corrigés avant ou pendant le développement de V1.0._

### 13.1 [CRITIQUE] Pas de vérification de l'intégrité du code core/

**Problème :** Un attaquant avec accès en écriture au serveur pourrait modifier un collecteur ou un module de `core/` pour exfiltrer les données (remplacer `sanitizer.py` par une version qui ne filtre rien, ou ajouter un collecteur `exfil.py` qui fait des requêtes HTTP).

**Solution :** Ajouter un mécanisme d'intégrité :
- Hash SHA256 de chaque fichier Python du projet, stocké dans `manifest.json` (global)
- Vérification des hashes au démarrage du run (mode `--verify-integrity`)
- Option `--audit-mode` qui log tous les imports et appels système sans écrire les JSON
- Publication du hash du dépôt Git dans le manifeste

---


---
### 13.8 [MINEUR] Aucune protection contre lesDoS logs

**Problème :** Si un collecteur génère des milliers de changements à chaque run (par exemple un système de fichiers très actif), le répertoire `changes/` peut croître très vite malgré la purge FIFO. Chaque fichier de changement contient la liste complète des changements du run — si un run génère 10 000 changements, le fichier peut faire plusieurs Mo et saturer le disque.

**Solution :** Implémenter une limite de taille par fichier de changement :

- Ajouter `config.changes.max_file_size_kb: 2048` (défaut: 2 Mo)
- Si le fichier dépasse la limite, troncature avec un champ `truncated: true` et un champ `changes_summary` (les 100 premiers changements + le total)
- Ajouter un champ `total_changes_truncated` dans le manifeste

---


---
### 13.9 [MINEUR] Pas de moyen de faire un "dry run" documenté

**Problème :** Le flag `--dry-run` n'existe pas encore dans le MVP. Un utilisateur qui veut tester un collecteur sans persister les données doit actuellement bidouiller avec `config.yaml` ou créer un répertoire temporaire.

**Solution :** Ajouter le flag `--dry-run` à `collector.py` :

```bash
python collector.py --run --dry-run --collector cpu
# → Exécute le collecteur cpu, affiche le JSON normalisé
# → Ne persiste rien dans knowledge/ ou changes/
# → Log normal
```

---


---
### 13.10 [MINEUR] Pas de moyen de paralléliser les collecteurs

**Problème :** Tous les collecteurs s'exécutent séquentiellement. Sur un serveur avec beaucoup de cœurs CPU et beaucoup de collecteurs, on sous-exploite les ressources. Le temps total du run est la somme des temps de chaque collecteur.

**Solution (V1.1+) :** Ajouter un mode parallèle optionnel :

```yaml
# config.yaml
collectors:
  parallel: true
  max_workers: 4  # Nombre de collecteurs并行执行
```

Implémentation via `concurrent.futures.ThreadPoolExecutor` — les collecteurs qui utilisent `subprocess` bénéficient automatiquement du parallélisme I/O (GIL Python libéré pendant les appels système). Les collecteurs qui font du CPU-intensif (calcul de hash, parsing) restent limités par le GIL — utiliser `ProcessPoolExecutor` pour ceux-ci si besoin.

---


---
### 13.12 [RISQUE] Gestion de l'UTF-8 et des caractères spéciaux

**Problème :** Les systèmes Linux peuvent avoir des noms de processus, de fichiers ou de containers contenant des caractères UTF-8 non-ASCII (noms en cyrillique, emoji dans les noms Docker, etc.). Si le parsing ne gère pas correctement l'encodage, le JSON de sortie peut être invalide.

**Solution :** standardiser sur UTF-8 everywhere :
- `subprocess` avec `encoding="utf-8", errors="replace"`
- Validation du JSON de sortie avec `json.loads()` avant écriture (levée `SchemaValidationError` si invalide)
- Le sanitizer doit être UTF-8-safe (ne pas tronquer de caractères multi-octets)

---


---

### 13.2 À implémenter avant V1.0

_Cette sous-section recense les mécanismes nécessaires pour une première version stable. Leur absence compromettrait la fiabilité ou la sécurité de V1.0._

### 13.2 [MAJEUR] Fichier de lock problématique sur NFS / /tmp en mémoire

**Problème :** Le fichier de verrou par défaut (/run/aicollector/aicollector.lock) pose un problème potentiel :
1. Sur NFS, la sémantique de locking (`fcntl`) peut ne pas fonctionner correctement (race conditions rares mais réelles)
2. `/tmp` est souvent mounté en `tmpfs` (mémoire), ce qui n'est pas un problème en soi mais le fichier disparaît au reboot — si un run est tué par OOM killer, le lockfile est automatiquement supprimé, ce qui élimine la protection

**Solution :** Configurer par défaut le lockfile dans le répertoire du projet (`$AICOLLECTOR_HOME/aicollector.lock`) plutôt que dans `/tmp/`. Ajouter un avertissement si `/tmp` est détecté comme `tmpfs` avec un conseil de déplacer le lockfile. Documenter la configuration NFS.

---


---
### 13.4 [MAJEUR] Comparaison SHA256 insuffisante pour les listes ordonnées

**Problème :** Le SHA256 est calculé sur du JSON canonique (clés triées). Mais les listes (arrays JSON) sont parcourues dans l'ordre du filesystem ou de la commande système. Si Docker renvoie les containers dans un ordre différent entre deux runs (ce qui arrive), le hash sera différent même si les données sont identiques en substance. Cela génère des faux positifs `modified`.

**Solution :** Ajouter une étape de **canonicalisation des listes** avant le calcul du hash :
- Pour chaque liste dans le JSON, trier les éléments par un identifiant stable (ex: `id`, `name`, `container_id`)
- Les listes qui n'ont pas d'identifiant naturel sont triées par hash des éléments
- Cela élimine les faux positifs sans changer la sémantique des données
- Coût : ~5 ms supplémentaire par collecteur

---


---
### 13.5 [MODÉRÉ] Absence de support pour les environnements à PHP rendah

**Problème :** La plateforme cible est Ubuntu 26.04 LTS. Cependant, beaucoup de serveurs fonctionnent avec des slices cgroup限制了 ou des контейнеры Docker qui limitent les ressources (CPU, mémoire, /proc/sys). Les collecteurs qui lisent `/proc/` peuvent être incomplets ou lents dans ces environnements.

**Solution :** Ajouter une variable d'environnement `AICOLLECTOR_CGROUP_MODE=true` qui :
- Limite le nombre de进程analysés dans `ps`
- Réduit les timeouts proportionnellement
- Ajoute un champ `cgroup_limited: true` dans chaque JSON collecté
- Documente les limitations dans les `inconsistencies_detected`

---


---
### 13.6 [MODÉRÉ] server_uuid non persisté entre réinstallations

**Problème :** `server_uuid` est généré une fois (via `uuid.uuid4()`) et stocké dans `config.yaml` (champ `server_uuid`). Si le fichier `config.yaml` est supprimé ou réinitialisé, un nouveau UUID est généré, cassant la continuité de l'historique. Tous les fichiers de `knowledge/` référencent cet UUID — ils deviennent incohérents.

**Solution :** Stocker le `server_uuid` dans un fichier distinct, inviolable, hors de `config.yaml` :

```
/var/lib/aicollector/.aicollector_uuid   ← fichier texte, chmod 600, non versionné git
```

Ce fichier est créé au premier run et jamais modifié. Il est lu avant `config.yaml`. La suppression de ce fichier constitue une rupture de continuité — un avertissement est émis mais le run continue (nouveau UUID généré).

---


---
### 13.7 [MODÉRÉ] Pas de versioning des collecteurs dans le manifeste de connaissance

**Problème :** Quand un collecteur change de version (incrémente `collector_version` ou `schema_version`), le manifeste `knowledge/manifest.json` indique la nouvelle version mais ne garde pas trace de l'ancienne. Si un agent IA lit des JSON de collecteurs de versions différentes, il ne sait pas à partir de quand la nouvelle structure s'applique.

**Solution :** Ajouter un champ `version_history` dans chaque entrée du manifest :

```json
"cpu": {
  "version_history": [
    {"collector_version": "1.0.0", "schema_version": "1.0", "since": "2026-07-03T16:34:00Z"},
    {"collector_version": "1.0.1", "schema_version": "1.0", "since": "2026-07-15T08:00:00Z"}
  ]
}
```

---


---
### 13.13 [MAJEUR] Permissions mal configurées à l'installation

**Problème :** Si le script d'installation attribue par erreur des permissions trop larges (par exemple `chmod 777` sur `/var/lib/aicollector/`) ou oublie de créer l'utilisateur système `aicollector`, les collecteurs könnten s'exécuter avec des droits insuffisants ou, à l'inverse, un attaquant local pourrait modifier les données de `knowledge/`.

**Solution :** Le script `install.sh` applique une politique de permissions stricte et documentée :
- `/opt/aicollector/` → `root:root`, `755` (lecture seule pour `aicollector`, nécessaire pour les imports Python)
- `/var/lib/aicollector/knowledge/` → `aicollector:aicollector`, `750`
- `/var/lib/aicollector/history/` → `aicollector:aicollector`, `750`
- `/var/lib/aicollector/changes/` → `aicollector:aicollector`, `750`
- `/var/cache/aicollector/` → `aicollector:aicollector`, `755`
- `/var/log/aicollector/` → `aicollector:aicollector`, `750`
- `/etc/aicollector/` → `root:root`, `644` (le code lit la config, pas la modifies)
- Le binaire Python (`collector.py`) → `aicollector:aicollector`, `755` (exécutable par le cron user)
- Vérification post-install avec `--check-permissions` (dry-run : affiche ce qui serait fait sans appliquer)

---


---
### 13.14 [MAJEUR] Script d'installation non idempotent

**Problème :** Un script d'installation qui échoue à mi-chemin (panne réseau, coupure électrique) et qu'on relance peut créer des doublons (utilisateur `aicollector` déjà existant → `useradd` échoue, répertoires déjà existants → plantage). À l'inverse, un script mal conçu pourrait détruire des données existantes.

**Solution :** `install.sh` est conçu pour être parfaitement idempotent :
- `useradd --system` → skip silencieux si l'utilisateur existe déjà
- Création de répertoires → `mkdir -p` (ne échoue pas si existant)
- Toutes les étapes sont conditionnelles (test avant action)
- Option `--dry-run` qui affiche le plan d'exécution sans rien modifier
- Option `--force-reinstall` explicite pour écraser une installation existante (confirmation interactive)
- Code组分最小 : pas de téléchargement dynamique (tout est local), pas de scripts inline, auditabilité maximale

---


---
### 13.15 [MAJEUR] Conflit avec un utilisateur système "aicollector" préexistant

**Problème :** Un serveur tiers préexistant peut déjà avoir un utilisateur système nommé `aicollector` pour un usage différent (un autre logiciel, un script interne). Créer notre propre utilisateur avec ce nom entrerait en conflit.

**Solution :** Le script d'installation détecte ce cas :
- Vérifier si l'utilisateur `aicollector` existe avec `id aicollector`
- Si l'utilisateur existe avec un shell différent de `/usr/sbin/nologin` ou un home directory, afficher un avertissement et demander confirmation avant de procéder
- Documenter le comportement dans le README et le script lui-même
- Alternative : permettre de personnaliser le nom de l'utilisateur via la variable `AICOLLECTOR_USER` dans l'environnement ou un fichier de config

---


---
### 13.16 [MAJEUR] Volumétrie de l'historique qui croît indéfiniment

**Problème :** Même avec la purge FIFO par nombre de versions (`retention.history_versions: 50`), un serveur très actif peut accumuler des centaines de fichiers de changement dans `changes/` (un fichier par run × nombre de runs). Si `retention.changes_versions` n'est pas configuré, le répertoire `changes/` peut atteindre plusieurs Go. De même, sans politique de rétention temporelle, les snapshots de `history/` peuvent dater de plusieurs années.

**Solution :** Ajouter une politique de rétention granulaire configurable dans `config.yaml` :

```yaml
retention:
  # Par nombre de versions (comportement actuel — conserve les N derniers snapshots)
  history_versions: 50        # par collecteur
  changes_versions: 200       # nombre max de fichiers dans changes/

  # Par âge (nouveau — politique temporelle)
  history_max_age_days: 90    # purger les snapshots de plus de 90 jours
  changes_max_age_days: 30   # purger les changements de plus de 30 jours

  # Politique combinée : les deux conditions sont évaluées (AND)
  # → Un fichier est supprimé s'il dépasse l'âge OU le nombre max
```

Cette politique est appliquée à chaque run par `knowledge_store.py`. Les métriques de volumétrie sont loguées (`INFO : history/ = 12 fichiers, 3.2 Mo ; changes/ = 47 fichiers, 8.1 Mo`).

---


---
### 13.17 [MAJEUR] Chevauchement de cron si un run dépasse l'intervalle

**Problème :** Avec un cron configuré toutes les 2 heures (`0 */2 * * *`), si un run prend plus de 2 heures (serveur très chargé, collecteur lent), le cron déclenché à H+2 se déclenche alors que le run de H est encore en cours. Les deux processus tentent d'écrire dans `knowledge/` simultanément → corruption potentielle des JSON.

**Mitigation existante :** Le lockfile `/run/aicollector/aicollector.lock` (implémenté dans `core/lockfile.py`) est vérifié au démarrage de chaque run. Si un lockfile existe avec un PID encore actif, le nouveau run refuse de démarrer et sort en erreur (exit code 2).

**Amélioration recommandée :** Ajouter un message explicite dans la sortie du run bloqué :
```
AICollector: run déjà en cours (PID 12345 depuis 02:34:12, 3h14min).
Le lockfile /run/aicollector/aicollector.lock empêche le démarrage d'un second run.
Prochain déclenchement cron estimé : 2min.
```
Et documenter le comportement dans le README : "Si vos collecteurs dépassent régulièrement 2h, augmentez l'intervalle cron ou désactivez les collecteurs les plus coûteux."

---


---

### 13.3 Recommandé pour V2+

_Cette sous-section recense les améliorations qui modifient significativement l'architecture ou qui ne sont pas bloquantes pour la première version stable. Elles sont planifiées pour V1.1 ou V2._

### 13.3 [MAJEUR] L'historique est naïf (copies complètes)

**Problème :** Chaque version de l'historique est une copie complète du JSON. Avec 50 versions × 15 collecteurs × JSON moyens de 50 Ko, on arrive à ~37,5 Mo d'historique. Cela croît linéairement sans limite autre que `retention_versions`. Sur un serveur avec beaucoup de containers Docker ou de processus, un collecteur `docker.json` ou `syslogs.json` peut faire 5 Mo chacun.

**Solution :** Passer à un système de snapshots incrémentaux :
- Stocker le premier JSON complet, puis uniquement les diffs (类型 : `patch`) pour les versions suivantes
- Compresser les snapshots avec `zstd` ou `gzip` (option configurable)
- Alternative : utiliser un système de fichiers avec déduplication (Btrfs, ZFS) — documenter la configuration
- Limite de taille par fichier dans `config.yaml` (actuellement 10 Mo, vérifier que c'est respecté en écriture)

---


---
### 13.11 [IDÉENONTRÉ] Pas de collecteur pour les métadonnées Kubernetes

**Problème :** La plateforme cible est "serveur Linux" mais de plus en plus de serveurs Ubuntu hébergent des clusters Kubernetes. Un collecteur `kubernetes.py` qui interroge l'API Kubernetes locale (lecture seule, `kubectl get nodes`, `kubectl get pods -A`) serait extrêmement précieux pour une IA.

**Solution :** En V1.1+, ajouter un collecteur `kubernetes.py` qui utilise `kubectl` (whitelisté) ou `kubernetes` Python client (module optionnel) pour collecter :
- Nodes (ressources, labels, conditions)
- Pods par namespace (status, redémarrages, images)
- Services et ingresses
- ConfigMaps et Secrets (secrets masqués par le sanitizer)

L'absence de `kubernetes` client ou `kubectl` n'est pas une erreur — le collecteur est désactivé avec un warning.

---


---



---

## 13.4 Décisions techniques validées (2026-07-10)

Les analyses critiques des modules suivants ont été réalisées :

### 13.4.1 `registry.py` — Faillesys.path et pollution d'import

| Problème | Gravité | Correctif |
|---|---|---|
| Insertion de `sys.path` en index 0 (shadowing) | 🔴 Haute | Utiliser `importlib.util.spec_from_file_location` |
| Pollution du `sys.path` global | 🟡 Moyenne | Isoler l'import dans un contexte temporaire |
| `_discovered` comme attribut de classe | 🟡 Moyenne | Déplacer vers `_instance` singleton |
| Importations silencieusement ignorées | 🔴 Haute | Collecter et journaliser les erreurs |

### 13.4.2 `sanitizer.py` — Destruction du contexte et code mort

| Problème | Gravité | Correctif |
|---|---|---|
| Remplacement global de la chaîne entière | 🔴 Haute | Redaction partielle via `re.sub` sur match seul |
| Bug de réentrance (`re.sub` dans `substitution`) | 🔴 Bloquante | Créer `_compile_pattern` à la volée |
| Mixin `SanitizerMixin` non référencé (code mort) | 🟡 Moyenne | Supprimer ou implémenter le mixin |
| `SanitizerMixin.__init__` absent | 🟡 Moyenne | Ajouter ou Documenter le contrat |

### 13.4.3 `schemas.py` — NameError et incohérence de validation

| Problème | Gravité | Correctif |
|---|---|---|
| `Literal` non importé (`NameError`) | 🔴 Bloquante | Ajouter `Literal` à l'import `typing` |
| Schéma `RAMCollectorSchema` absent | 🔴 Bloquante | Créer et enregistrer le schéma |
| `_COLLECTOR_SCHEMA_REGISTRY` non exporté | 🟡 Moyenne | Exposer via `__all__` ou propriété |
| Type hints incohérents (`typed_dict` vs `BaseModel`) | 🟡 Moyenne | Uniformiser les types dans les schémas |

---

## Annexe — Glossaire

| Terme | Définition |
|---|---|
| **Collector** | Plugin qui implémente `BaseCollector` et collecte un type d'information système |
| **Pipeline** | Orchestrateur des 4 phases (COLLECT → NORMALIZE → COMPARE → KNOWLEDGE BASE) |
| **SystemAdapter** | Couche d'abstraction qui intercepte tous les appels système |
| **EventBus** | Bus d'événements synchrone in-process |
| **Knowledge Base** | Base de connaissances persistée dans `knowledge/` |
| **Changes** | Historique des détections de changement dans `changes/` |
| **History** | Versions historiques des JSON de chaque collecteur dans `history/` |
| **Server UUID** | Identifiant unique du serveur, généré une fois, persisté dans `.aicollector_uuid` |
| **Lockfile** | Fichier de verrou empéchant les runs simultanés |
| **Sanitizer** | Module de défense en profondeur qui remplace les secrets |
| **Canonical JSON** | JSON sérialisé avec clés triées et séparateurs stricts pour reproductibilité du hash |
| **FIFO** | First In, First Out — politique de rétention qui purge les entrées les plus anciennes |
| **NDJSON** | Newline-Delimited JSON — un objet JSON par ligne, utilisé pour les logs |
| **SHA256 Canonical Hash** | Hash SHA256 calculé sur du JSON canonique (clés triées, séparateurs normalisés) et préfixé `sha256:` (standard obligatoire du projet — Décision #6) |

| **dpkg-query** | Outil en ligne de commande pour interroger la base de données des paquets Debian/Ubuntu. Utilisé par le collecteur APT pour obtenir une liste délimitée et dénuée d'ambiguïté de tous les paquets installés. |
| **APT** | Advanced Packaging Tool — système de gestion de paquets d'Ubuntu/Debian. Le collecteur `apt` interroge à la fois `dpkg-query` (liste des installés) et `apt list` (mises à jour disponibles). |
