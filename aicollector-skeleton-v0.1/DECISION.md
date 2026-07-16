[DECISIONS.md](https://github.com/user-attachments/files/30080870/DECISIONS.md)
# DECISIONS D'ARCHITECTURE — AICollector
# Registre unifié — Version consolidée au 2026-07-09

> Registre des décisions techniques structurantes du projet. Les entrées existantes ne sont **jamais supprimées ni modifiées**, même renversées : leur statut reflète fidèlement l'historique. Ce document complète `SPECIFICATION.md` en fournissant un historique décisionnel explicite accessible en un coup d'œil.

> **Règle stricte** : Aucune décision n'est considérée comme appliquée dans `SPECIFICATION.md` tant que l'utilisateur n'a pas explicitement confirmé l'avoir intégrée dans son fichier réel.

---

*Sources fusionnées :*
- `DECISIONS.md` (initiale, 2026-07-06) — 2 décisions
- `DECISIONS(1).md` / `DECISIONS1.md` (2026-07-07) — +1 décision (#3)
- `DECISIONS2.md` (2026-07-08) — +7 décisions (#5-#11)
- `DECISIONS3.md` (2026-07-09) — +10 décisions (#1-#4, #6-#10) — document le plus complet

---

## Décision #1

| Champ | Valeur |
|---|---|
| **Date** | 2026-07-06 |
| **Sujet** | Langage de collecte |
| **Décision originale** | Les collecteurs effectuent la collecte en exécutant des commandes shell et en parsant leur sortie. |
| **Statut** | RENVERSÉE par Décision #2 |

---

## Décision #2

| Champ | Valeur |
|---|---|
| **Date** | 2026-07-07 |
| **Sujet** | Mécanisme d'exécution des collecteurs |
| **Décision** | Chaque collecteur est une classe Python concrète instanciable qui expose une méthode `collect() → list[CollectResult]`. Le `Pipeline` les invoque dans l'ordre via le `Registry`. Aucune exécution de script externe. Lancement dans des threads parallèles via `concurrent.futures.ThreadPoolExecutor`. |
| **Justification** | a) **Testabilité** : une classe Python est testable unitairement avec des mocks. b) **Type safety** : Python offre du typage statique, des dataclasses, et des exceptions cohérentes. c) **Performance** : pas de subprocess overhead pour chaque collecteur. d) **Extensibilité** : le décorateur `@collector` enregistre automatiquement le collecteur. e) Les collecteurs sont I/O-bound (latence commande), le threading est adapté et le GIL Python est acceptable car le temps est passé en wait syscall. |
| **Risques identifiés** | Ordre de résultats non déterministe (acceptable), complexité accrue du pipeline (atténué par un design modulaire). |
| **Alternatives éliminées** | API directes (trop complexe, non universel), scraping /proc (non portable), exécution de scripts Bash externes (difficile à tester en CI). |
| **Statut** | **ACTIVE** |
| **Documents impactés** | `core/base_collector.py`, `core/pipeline.py`, `core/registry.py`, `SPECIFICATION.md` §2, §3 |

---

## Décision #3

| Champ | Valeur |
|---|---|
| **Date** | 2026-07-07 |
| **Sujet** | Gestion conditionnelle du collecteur `auditd` en fonction de `config.yaml` |
| **Choix** | `auditd` est installé et activé **uniquement si** :\n- Le collecteur `auditd` est activé par défaut (liste blanche vide dans `collectors.enabled`), **ou**\n- `auditd` est explicitement listé dans `collectors.enabled` dans `config.yaml`.\n\nDans tous les autres cas (`auditd` dans `collectors.disabled`), le paquet `auditd` n'est ni installé ni activé. |
| **Justification** | Minimiser les dépendances inutiles, adapter l'installation à la configuration utilisateur, et éviter d'installer des outils système non sollicités. |
| **Statut** | **ACTIVE** |
| **Documents impactés** | `install.sh`, `uninstall.sh`, `check_dependencies.sh`, `DEPENDENCIES.md` |

---

## Décision #4

| Champ | Valeur |
|---|---|
| **Date** | 2026-07-07 |
| **Sujet** | Choix de la librairie de validation pour `core/config_loader.py` |
| **Choix initial** | `dataclasses` stdlib (aucune dépendance externe à Pydantic) |
| **Statut** | RENVERSÉE par Décision #5 |

---

## Décision #5

| Champ | Valeur |
|---|---|
| **Date** | 2026-07-07 |
| **Sujet** | Retour sur le choix de validation pour `core/config_loader.py` — Pydantic obligatoire |
| **Décision** | `pydantic` (≥2.x) **obligatoire** pour la configuration uniquement (`AICollectorConfig` et sous-modèles). Les autres structures du projet (`CollectorResult`, `CollectorCapabilities`, etc.) restent en `dataclasses` stdlib. |
| **Justification** | Éliminer toute validation manuelle ; fiabilité accrue du parsing/validation YAML ; Pydantic offre des messages d'erreur explicites et un typage fort sans alourdir le code. |
| **Statut** | **APPLIQUÉE** — confirmée par l'utilisateur le 2026-07-07 dans `SPECIFICATION` v1.1 |
| **Documents impactés** | `SPECIFICATION.md` §1.4 (Contraintes), §3.7 (`config_loader.py`), §11.2 (Standards de style — imports), §12 (Roadmap) |

---

## Décision #6

| Champ | Valeur |
|---|---|
| **Date** | 2026-07-08 |
| **Sujet** | Comportement de EventBus lors d'erreurs d'abonnés |
| **Décision** | L'émission d'événements est **non-bloquante** : si un abonné lève une exception, celle-ci est logguée localement mais **ne remonte pas dans l'émetteur**. Les abonnés restants sont quand même notifiés. Si **tous** les abonnés d'un événement échouent, un seul log de niveau ERROR est émis. |
| **Raison** | Le pipeline ne doit pas être interrompu par un subscriber défaillant. Le cas critique : phase de collecte échoue → `RUN_FAILED` émis → un abonné (ex: plugin Slack) bug → si l'exception remonte, elle écrase l'exception originale du pipeline (double-fault), brisant le diagnostic. Isoler les abonnés protège la stabilité du pipeline et préserve les erreurs critiques. |
| **Statut** | **ACTIVE** |
| **Documents impactés** | `core/event_bus.py`, `SPECIFICATION.md` §3.x |

---

## Décision #7

| Champ | Valeur |
|---|---|
| **Date** | 2026-07-08 |
| **Sujet** | Typage des erreurs non-bloquantes dans les collecteurs |
| **Décision** | Introduire une dataclass `CollectorErrorEntry` dans `base_collector.py` pour typer les erreurs non-bloquantes (timeout, permission refusée, commande introuvable). La structure `errors` dans `CollectorResult` devient `list[CollectorErrorEntry]` au lieu de `list[dict[str, Any]]`. Les collecteurs utilisent cette dataclass au lieu de dictionnaires bruts. |
| **Raison** | Les dictionnaires bruts pour les erreurs sont fragiles : chaque collecteur formate différemment, pas de contrat clair sur les clés requises. La dataclass impose un schéma standard validé en phase de normalisation du pipeline, garantissant cohérence et diagnosticabilité. |
| **Statut** | **ACTIVE** |
| **Documents impactés** | `core/base_collector.py`, `SPECIFICATION.md` §3.x |

---

## Décision #8

| Champ | Valeur |
|---|---|
| **Date** | 2026-07-08 |
| **Sujet** | Hiérarchie d'exceptions et sémantique de bloquage |
| **Décision** | Rendre l'attribut `_is_blocking` **public** (renommé en `is_blocking`) et ajuster `CommandExecutionError` pour qu'il soit **non-bloquant** (`is_blocking = False`). Cela permet au pipeline de continuer une collecte même en cas d'erreur d'exécution d'une commande individuelle. `CommandExecutionError` en non-bloquant permet une collecte partielle : si une commande échoue sur un collecteur donné, les autres collecteurs continuent, et le résultat inclut l'erreur formatée dans `CollectorResult.errors`. |
| **Contexte technique** | `CommandExecutionError` est levée quand une commande collecteur échoue avec un exit code non nul. Elle hérite indirectement de `CollectorError` (non-bloquant), mais son statut était ambigu. La rendre explicitement non-bloquante clarifie le contrat : le pipeline n'est pas interrompu, l'erreur est capturée dans le résultat. |
| **Documents impactés** | `core/exceptions.py`, `pipeline.py`, `SPECIFICATION.md` §3.x |
| **Statut** | **ACTIVE** |

---

## Décision #9

| Champ | Valeur |
|---|---|
| **Date** | 2026-07-08 |
| **Sujet** | Robustesse du calcul de hash JSON et standard de format |
| **Décision** | 1) Ajouter un `default` encoder à `compute_json_hash` utilisant une stratégie de conversion automatique (`isoformat()` pour les `datetime`, `str()` pour les types non-sérialisables) pour éviter les `TypeError` silencieux lors du hashing de données contenant des types non-JSON-natifs. 2) Documenter le préfixe `sha256:` comme **standard obligatoire** pour tout hash généré par le projet. |
| **Justification** | a) **Crash prévention** : `json.dumps()` lève `TypeError` sur `datetime`, `Path`, `set`, `Enum`, etc. En phase de collecte ou de normalisation, un seul champ mal typé fait crasher l'intégralité du calcul de hash. Le fallback encoder garantit que le hash est toujours calculable. b) **Standard cohérent** : le préfixe `sha256:` permet d'identifier rapidement l'algorithme dans les manifestes et les logs, et prépare une future extensibilité multi-algorithme (sha512, blake2). |
| **Contexte technique** | Pour `datetime`: `value.isoformat()` renvoie une string ISO 8601 standard. Pour les types résiduels: `str(value)` est un dernier recours qui préserve l'information (ex: `set` → `"{'a', 'b'}"`). Ces conversions sont déterministes pour `datetime` mais non déterministes pour `set` (ordre aléatoire) — ce dernier cas doit être détecté et signalé explicitement par un warning. |
| **Documents impactés** | `core/hashing.py`, `SPECIFICATION.md` §3.x |
| **Statut** | **ACTIVE** |

---

## Décision #10

| Champ | Valeur |
|---|---|
| **Date** | 2026-07-08 |
| **Sujet** | Persistance atomique, rotation FIFO, et manifestes dans KnowledgeStore |
| **Décision** | Trois changements simultanés pour `knowledge_store.py` : |
| | 1. **Écriture atomique POSIX** : toutes les écritures de fichiers JSON passent par un fichier temporaire + `os.replace()` (atomic rename) pour éviter les fichiers corrompus en cas de crash ou de cut-off en cours d'écriture. |
| | 2. **Rotation physique FIFO avec index-shift** : l'historique est limité physiquement de `0001` à `max_versions`. Quand la limite est atteinte, les fichiers sont renommés (shift) : `0002→0001, 0003→0002, ..., max→removed`, éliminant physiquement le plus ancien. L'index ne dérive pas. |
| | 3. **Intégration automatique des manifestes** : `write_knowledge()` met à jour `knowledge/manifest.json` et `write_change()` met à jour `changes/manifest.json` atomiquement, en incluant hash, timestamp, et métadonnées. |
| **Justification** | a) **Atomicité** : sans fichier temporaire, un `write()` partial suivi de crash laisse un JSON invalide sur disque, brisant tous les parsers suivants. b) **Index-shift vs gap-fill** : fill-from-0001 fait grossir les index indéfiniment. L'index-shift garantit des noms de fichiers bornés, facilite la gestion externe (scripts, monitoring). c) **Manifestes** : sans manifeste, une IA ou un système externe doit scanner le disque entier pour connaître l'état. |
| **Contexte technique** | `os.replace()` est atomique sur POSIX et presque atomique sur Windows (garanti depuis Python 3.3). Le fichier temporaire utilise `NamedTemporaryFile` avec `mode='w'`, `encoding='utf-8'`, `delete=False`, fermé avant le `replace()`. Les manifestes sont des dictionnaires `dict[collector_name] → MetadataEntry` pour lookup O(1). |
| **Documents impactés** | `core/knowledge_store.py`, `SPECIFICATION.md` §3.x, §4 |
| **Statut** | **ACTIVE** |

---

## Décision #11

| Champ | Valeur |
|---|---|
| **Date** | 2026-07-08 |
| **Sujet** | Mécanisme de défense en profondeur (sanitizer) |
| **Décision** | Deux couches de sanitization : 1) **Couche dure (post-traitement)** : sanitizer posteudit les résultats de chaque collecteur avant écriture — expressions régulières qui remplacent les secrets par `***REDACTED***`. 2) **Couche douce (pré-traitement)** : chaque collecteur peut intégrer une sanitization interne pour des besoins spécifiques. Les deux couches sont actives. |
| **Justification** | a) **Défense en profondeur** : si un collecteur oublie de sanitizer un secret, la couche dure ratisse le filet. b) **Couche douce** : permet des sanitizations contextuelles intelligentes (ne pas cacher une IP si c'est précisément l'objet de la collecte). c) **Couche dure** : garantit un scénario de données safe-for-LLM quoi qu'il arrive. |
| **Documents impactés** | `core/sanitizer.py`, `core/pipeline.py` (phase NORMALIZE), `SPECIFICATION.md` §3.8 |
| **Statut** | **ACTIVE** |

---

## Décision #12

| Champ | Valeur |
|---|---|
| **Date** | 2026-07-09 |
| **Sujet** | Format d'échange interne et stratégie de hashage |
| **Décision** | Toutes les données transitent en JSON canonique (clés triées, séparateurs normalisés). Le hash de comparaison est calculé via SHA-256 sur le JSON canonique, préfixé `sha256:` (standard obligatoire du projet). |
| **Justification** | a) **Reproductibilité** : `json.dumps(obj, sort_keys=True)` garantit que deux sérialisations du même dict produisent exactement la même chaîne — donc le même hash. b) **Performance** : SHA-256 est rapide et disponible nativement via `hashlib`. c) **Standard** : `sha256:` est explicite et permet de changer d'algorithme sans ambiguïté (`blake2b:`, `sha512:`). d) **Interopérabilité** : tout système externe peut recalculer le hash en lisant le JSON. |
| **Documents impactés** | `core/hashing.py`, `core/pipeline.py`, `SPECIFICATION.md` §3.2 |
| **Statut** | **ACTIVE** |

---

## Décision #13

| Champ | Valeur |
|---|---|
| **Date** | 2026-07-09 |
| **Sujet** | Hiérarchie d'exceptions et gestion des erreurs |
| **Décision** | Cinq exceptions spécifiques héritées de `AICollectorError` : `CollectorError`, `ForbiddenCommandError`, `CommandExecutionError`, `ProcFileReadError`, `LockfileError`. Chaque exception transporte un contexte (run_id, nom du collecteur, détails). Le pipeline arrête le run en cas d'erreur fatale et logue avant de quitter. |
| **Justification** | a) **Granularité** : un collecteur peut échouer (permission, élément manquant) sans impacter les autres. b) **Traçabilité** : le contexte dans l'exception (run_id, nom) permet de corréler un log à une exécution spécifique. c) **Découplage** : le pipeline n'a pas besoin de connaître le type d'erreur pour arrêter proprement. |
| **Documents impactés** | `core/exceptions.py`, `core/pipeline.py`, tous les collecteurs, `SPECIFICATION.md` §3.1 |
| **Statut** | **ACTIVE** |

---

## Décision #14

| Champ | Valeur |
|---|---|
| **Date** | 2026-07-09 |
| **Sujet** | Durcissement de SystemAdapter — Validation des arguments, atomicité du cache, et whitelisting des chemins |
| **Statut** | **ACTIVE** |
| **Décision** | Trois changements simultanés pour `system_adapter.py` : |

**1. Validation stricte des arguments de commande**

Tous les éléments de `args` passés à `run_command()` sont validés :
- Interdiction de tout argument contenant des caractères de shell dangereux : `` &`$\\|><"' `` et `;`
- Interdiction de toute redirection (`>`, `>>`, `2>`, `2>&1`, `<`)
- Interdiction de tout motif d'injection de subshell (`$(`, `` ` ``)
- Les arguments numériques sont autorisés si composés uniquement de digits

Si un argument invalide est détecté → `ForbiddenCommandError` immédiate.

**2. Suppression du cache intra-run pour les résultats de commande système**

Le cache `self._cache` pour les résultats de `run_command()` est supprimé :
- Les résultats de collecteurs qui appellent `run_command()` ne doivent **jamais** être servis depuis un cache (risque de servir des données obsolètes)
- Conservation optionnelle du cache uniquement pour `read_proc_file()` / `read_sys_file()` avec une TTL courte explicite (via timestamp d'acquisition)
- Clé de cache = tuple `(path, mtime)` pour invalidation automatique sur modification du fichier sous-jacent

**3. Whitelist des chemins /proc/ et /sys/ pour `read_proc_file()` et `read_sys_file()`**

Les chemins lus via ces méthodes sont validés contre une whitelist de préfixes :
```python
_ALLOWED_PROC_PREFIXES: frozenset[str] = frozenset({
    "/proc/", "/sys/", "/dev/", "/sys/class/net/",
})
```
Si le chemin ne figure pas dans la whitelist → `ForbiddenCommandError` immédiate.

**Justification :**

a) **Arguments non validés = whitelist contournable** : Des commandes whitelistées comme `find`, `awk` ou `grep` possèdent des options d'exécution de commandes externes (`-exec`, `system()`, `-F 'BEGIN{system()}'`). Un argument malveillant pourrait contourner la whitelist en exécutant du code arbitraire via ces options. La validation d'arguments est la deuxième ligne de défense indispensable.

b) **Cache = données potentiellement obsolètes** : Un cache intra-run pour des données système dynamiques (`ss`, `ps`, `df`) peut servir des informations périmées entre deux appels du même run. Pour les collecteurs, chaque appel doit refléter l'état réel du système.

c) **Whitelist de chemins = isolation filesystem** : Sans whitelist sur les chemins `/proc/` et `/sys/`, un collecteur malveillant ou bogué pourrait lire n'importe quel fichier via `read_proc_file()` : `/proc/self/environ`, `/proc/self/cmdline`, `/etc/shadow`. La whitelist confine les lectures au périmètre strictement nécessaire.

**Contexte technique :**
- Validation des arguments via regex `^[a-zA-Z0-9./_:-]+$` (aucun métacaractère shell)
- Option `--skip-argument-validation` en mode dev pour diagnostiquer les false positives
- `_forbidden_args_pattern` compilé une fois à l'initialisation de la classe (pas de regex par appel)
- Cache basé sur `(path, mtime)` : `os.stat(path).st_mtime` vérifié avant de servir un résultat cached

**Documents impactés :** `core/system_adapter.py`, `SPECIFICATION.md` §3.3 |

---

## Décision #15

| Champ | Valeur |
|---|---|
| **Date** | 2026-07-09 |
| **Sujet** | Politique de logger — Niveaux séparés, atomicité NDJSON, sanitization, et contextualisation |
| **Statut** | **ACTIVE** |
| **Décision** | Quatre changements simultanés pour `logger.py` : |

**1. Correction du niveau du logger root (bug de configuration)**

Le logger root (`aicollector`) est configuré au niveau le plus bas (`DEBUG`) et chaque handler définit son propre niveau filtrant :
- `console_handler` → `INFO` (affiche les événements significatifs)
- `file_handler` → `DEBUG` (capture tout, y compris les lignes de debug)
- Le logger root **ne filtre aucun message** — la décision de propagation revient aux handlers

```python
logger.setLevel(logging.DEBUG)  # Ne filtre rien — délègue aux handlers
console_handler.setLevel(logging.INFO)
file_handler.setLevel(logging.DEBUG)
```

**2. Protection contre l'injection NDJSON**

Les valeurs dans les logs NDJSON sont sanitizées avant sérialisation :
- Chaque valeur de log (msg, extra) est parcourue récursivement
- Les sauts de ligne `\n` et `\r` sont échappés en `\u000a` / `\u000d`
- Les backslashes `\`, les guillemets `"`, et autres caractères de contrôle sont échappés
- Les secrets détectés (via le sanitizer du projet) sont remplacés par `***REDACTED***`

**3. Sanitization des credentials dans les logs**

Le logger intègre un sanitizer intégré léger (liste de patterns) appliqué à toutes les valeurs avant sérialisation :
- Pattern `password=...`, `token=...`, `secret=...`, `api_key=...` → remplacement de la valeur par `***REDACTED***`
- Pattern Bearer tokens dans les URLs → `***REDACTED***`
- Application **après** l'échappement NDJSON pour éviter les faux positifs dans les caractères échappés

**4. Contextualisation obligatoire (run_id + timestamp ISO)**

Chaque ligne NDJSON inclut automatiquement :
- `run_id` : UUID du run (injecté via `LoggerAdapter`)
- `timestamp_iso` : datetime ISO 8601 avec timezone UTC
- `level` : niveau du log (DEBUG, INFO, WARNING, ERROR)
- `module` : nom du module source

Le `run_id` est injecté par le `LoggerAdapter` sans modifier la signature des appels.

**Justification :**

a) **Bug de niveau root** : Un logger configuré à `INFO`拦截 tous les messages `DEBUG`, rendant le `file_handler` configuré à `DEBUG` inefficace. Le logger root doit être au niveau le plus bas possible pour ne pas filtrer.

b) **Injection NDJSON** : Si un collecteur logue un message contenant des données binaires ou des sauts de ligne involontaires, la sérialisation JSON standard peut produire un JSON invalide. L'échappement garantit une ligne = un objet JSON valide.

c) **Sanitization des credentials** : Les collecteurs peuvent involontairement loguer des tokens, passwords, ou clés API. Sans sanitizer intégré au logger, ces secrets fuient vers les fichiers de log.

d) **Contextualisation** : Sans `run_id` et `timestamp` dans chaque ligne, il est impossible de corréler un log à un run spécifique en production.

**Contexte technique :**
- `LoggerAdapter` subclass qui ajoute `run_id` et `timestamp_iso` à chaque `process()`
- `default=str` dans `json.dumps()` pour convertir les objets non-sérialisables en strings
- Pas de `ensure_ascii=True` (garder l'UTF-8 pour les messages français)
- Rotation : `TimedRotatingFileHandler` avec `when='midnight'`, `backupCount=7`

**Documents impactés :** `core/logger.py`, `SPECIFICATION.md` §3.x |

---

## Décision #16

| Champ | Valeur |
|---|---|
| **Date** | 2026-07-09 |
| **Sujet** | Atomicité et robustesse du lockfile — Race condition corrigée, atomicité POSIX, et signal valide |
| **Statut** | **ACTIVE** |
| **Décision** | Trois changements pour `lockfile.py` : |

**1. Acquisition atomique via `os.open()` avec `O_EXCL|O_CREAT`**

L'acquisition du lockfile utilise `os.open(path, os.O_CREAT|os.O_EXCL, 0o644)` :
- `O_EXCL` garantit que l'appel échoue si le fichier existe déjà — **atomicité complète**
- Suppression de `open()` + `exists()` + `os.kill()` + `open()` (qui était une race condition)
- Le seul window de race condition est la vérification de `_process_alive()` — qui ne peut pas être atomic avec le fichier, mais qui est accepté car il ne s'agit que de détection, pas de création

**2. Correction de la détection de processus vivante (`os.kill(pid, 0)`)**

Remplacement de l'utilisation invalide de `signal.SIG_DFL` :
```python
# ❌ Incorrect (avant)
os.kill(pid, signal.SIG_DFL)

# ✅ Correct (après)
os.kill(pid, 0)
```
`os.kill(pid, 0)` est la technique POSIX standard pour vérifier si un PID existe sans lui envoyer de signal. Elle retourne sans erreur si le processus existe, et lève `OSError(ESRCH)` si le PID n'existe pas ou est un zombie.

**3. Gestion du processus initiateur (PID 1 — init/systemd)**

Si le lockfile contient PID 1, le run est autorisé à se poursuivre :
- PID 1 (`init`/`systemd`) ne peut pas mourir brutalement — il est toujours vivant
- Mais si `/proc/1/cmdline` ne contient pas `systemd` ou `init`, le lockfile est considéré comme invalide et réécrit

**Justification :**

a) **Race condition (acquisition non atomique)** : Avec `open()` puis `exists()`, un second processus peut créer le fichier entre la vérification et la création. `O_EXCL` résout ce problème à la primitive système.

b) **`SIG_DFL` invalide** : `signal.SIG_DFL` n'est pas un signal réel — c'est une constante Python. Son utilisation dans `os.kill()` est un behavior indéterminé. `os.kill(pid, 0)` est la méthode standard, portable et sans effet secondaire.

c) **PID 1 comme cas limite** : Un lockfile créé par un processus qui meurt en laissant PID 1 dans le fichier serait incorrectement considéré comme valide car PID 1 est toujours vivant. La vérification supplémentaire de `/proc/1/cmdline` ferme cette porte.

**Contexte technique :**
- `os.open()` nécessite un `fd` qui doit être fermé avec `os.close(fd)` après écriture
- `O_EXCL` sur un système de fichiers NFS peut ne pas être atomique selon la config serveur
- Signal number `0` est le seul "signal" garanti par POSIX pour tester l'existence sans effets

**Documents impactés :** `core/lockfile.py`, `SPECIFICATION.md` §3.x |

---
## Décision #17

| Champ | Valeur |
|---|---|
| **Date** | 2026-07-10 |
| **Sujet** | Validation Pydantic — correction NameError, conflit source `ram`, et capacités typées |
| **Statut** | **ACTIVE** |
| **Documents impactés** | `core/schemas.py`, `core/base_collector.py`, `SPECIFICATION.md` §3.8 |

**Trois corrections simultanées pour `schemas.py` :**

**1. Correction du `NameError` sur `Literal`**

`Literal` est utilisé dans les schémas pour typer le champ `source` (ex: `source: Literal["cpu"]`), mais le module `typing` n'était pas explicitement importé. L'exécution de `schemas.py` plantait avec `NameError: name 'Literal' is not defined`.

→ Solution : ajouter `Literal` à la ligne d'import du module `typing` :
```python
from typing import Annotated, Any, Literal, Union
```

**2. Résolution du conflit de validation pour le collecteur `ram` (plantage systématique)**

`RAMCollectorSchema` était enregistré sous la clé `"ram"`, mais le champ `source` dans le schéma valait `"memory"` (valeur historique). Lors de la validation avec `validate_knowledge_json(data)` :
- Le registre retrouvait le schéma via la clé `"ram"` → `RAMCollectorSchema`
- Mais le champ `source` du dict à valider valait `"memory"`
- `RAMCollectorSchema` attendait `source: Literal["ram"]` → **échec de validation**
- `validate_knowledge_json()` tombait dans le fallback `return data` (raw dict)

Ce contournement silencieux rendait la validation ineffective pour le collecteur `ram`, laissant passer des données non validées dans la base de connaissances.

→ Solution : deux options possibles (choisir une) :
- Option A (recommandée) : aligner le `source` du collecteur `ram` vers `"ram"` dans son implémentation
- Option B : utiliser un schéma de base `CollectorSchema` avec `source: str` non contraint pour `ram`

**3. Introduction du typage fort sur `CollectorCapabilities`**

La méthode `capabilities()` de `BaseCollector` renvoyait une `CollectorCapabilities` non typée en Pydantic. Le champ `known_inconsistencies: list[str]` et les nouvelles capacités (`CollectorCapabilities`) étaient absentes du registre de schémas, rendant l'introspection statique impossible et bloquant les vérifications mypy.

→ Solution : typer explicitement `capabilities` dans les schémas via un sous-modèle optionnel, et documenter le champ `capabilities` dans tous les schémas individuels :
```python
class CPUCollectorSchema(BaseModel):
    # ...
    capabilities: CPUCapabilities | None = None
```

**Justification :**

a) **NameError** : tout module Python qui utilise `Literal` sans l'importer est un bug latent bloquant. La correction est triviale et sans risque.

b) **Conflit source** : un système de validation qui échoue silencieusement et retourne le dict brut est une **fausse sécurité**. L'agent IA qui interroge la base de connaissances ne peut pas faire confiance aux données du collecteur `ram`. La correction du conflit garantit que `ram` est validé comme tous les autres collecteurs.

c) **Capacités typées** : typer les capacités des collecteurs permet à mypy de détecter les incohérences à la compilation, et à l'agent IA de comprendre les limitations-known d'un collecteur lors de l'interrogation.

---

*Dernière mise à jour : 2026-07-09 — Document unifié à partir de 4 sources distinctes (DECISIONS.md, DECISIONS2.md, DECISIONS3.md, DECISIONS(1).md). 17 décisions séquentielles, aucune supprimée.*

---

## Decision #18

| Champ | Valeur |
|---|---|
| **Date** | 2026-07-10 |
| **Sujet** | Robustesse du pre-vol self_diagnostic -- controle disque sur le repertoire parent |
| **Statut** | **ACTIVE** |
| **Documents impactes** | core/self_diagnostic.py, SPECIFICATION.md SS8 |

shutil.disk_usage(base_dir) leve FileNotFoundError au premier demarrage si le repertoire n'existe pas. Decision : verifier l'espace disque sur base_dir.parent (qui existe toujours), pas sur base_dir lui-meme.

```python
# CORRECT : le parent existe toujours
stat = shutil.disk_usage(base_dir.parent)

# INCORRECT : base_dir peut ne pas exister
stat = shutil.disk_usage(base_dir)
```

---

## Decision #19

| Champ | Valeur |
|---|---|
| **Date** | 2026-07-10 |
| **Sujet** | Entree systemd aicollector.timer comme mecanisme de planification principal |
| **Statut** | **ACTIVE** |
| **Documents impactes** | scripts/install.sh, SPECIFICATION.md SS3.5 |

Unite systemd timer en complement du cron existant.

```ini
[Timer]
OnBootSec=5min
OnUnitActiveSec=2h
Persistent=true
```

Le timer est active par defaut. Persistent=true compense les declenchements manques. Cron reste en fallback.

---

## Decision #20

| Champ | Valeur |
|---|---|
| **Date** | 2026-07-10 |
| **Sujet** | Robustesse de uninstall.sh -- ordre execution, userdel, PID 1 |
| **Statut** | **ACTIVE** |
| **Documents impactes** | scripts/uninstall.sh, SPECIFICATION.md SS3.6 |

1. Supprimer --remove-home de userdel. Les repertoires de donnees sont supprimes par rm -rf explicites.

```bash
# CORRECT
userdel aicollector
rm -rf /var/lib/aicollector/ /opt/aicollector/

# INCORRECT
userdel --remove-home aicollector
```

2. stop_services AVANT uninstall_processes (ordre corrige).

3. PID 1 dans le lockfile = invalide si /proc/1/cmdline ne contient pas systemd/init.

---

## Decision #21

| Champ | Valeur |
|---|---|
| **Date** | 2026-07-10 |
| **Sujet** | Configuration mypy stricte et integration pytest-cov |
| **Statut** | **ACTIVE** |
| **Documents impactes** | pyproject.toml, tests/, core/, collectors/ |

```toml
[tool.mypy]
python_version = "3.12"
strict = true
warn_return_any = true
warn_unused_ignores = true
ignore_missing_imports = true
```

strict = true active tous les checks de type. Tout fichier doit passer mypy sans erreur.

```toml
[project.optional-dependencies]
dev = ["pytest>=8.0", "pytest-cov>=4.0", "mypy>=1.0"]
```

Objectifs : core >= 80%, collecteurs >= 70%, logger/sanitizers >= 90%.

---

## Décision #22

| Champ | Valeur |
|---|---|
| **Date** | 2026-07-15 |
| **Sujet** | Scission de la validation des arguments en deux filtres distincts |
| **Statut** | **ACTIVE** |

**Contexte :** Le motif `sh` était vérifié par simple sous-chaîne (`pattern in lower_arg`). Cela bloquait légitimement des arguments `--show` ou `dash` contenant cette séquence, générant des `ForbiddenCommandError` pour des collecteurs parfaitement valides.

**Solution :** Séparer `DANGEROUS_ARG_PATTERNS` en deux ensembles distincts dans `system_adapter.py` :

**1. `DANGEROUS_SUBSTRING_PATTERNS`** — vérifié par `pattern in lower_arg` :
```python
DANGEROUS_SUBSTRING_PATTERNS: Final[frozenset[str]] = frozenset({
    "-exec", "system", "eval", ">", "<", "|", ";", "&&", "||"
})
```
→ Inclut les opérateurs de contrôle shell, redirections et motifs d'exécution dangereux.

**2. `DANGEROUS_EXECUTABLE_NAMES`** — vérifié par `exact match` ou `/sh` suffix :
```python
DANGEROUS_EXECUTABLE_NAMES: Final[frozenset[str]] = frozenset({
    "sh", "bash", "python", "perl", "ruby", "php",
    "nc", "ncat", "curl", "wget"
})
```
→ Inclut les interpréteurs et outils réseau suspects. Le suffixe `/sh` est également vérifié pour parer à `/bin/sh`.

**Nouvelle logique de `_validate_safe_args` :**
```python
for arg in args:
    lower_arg = arg.lower()
    # Filtre A : sous-chaînes interdites (injection, contrôle shell)
    if any(pattern in lower_arg for pattern in DANGEROUS_SUBSTRING_PATTERNS):
        raise ForbiddenCommandError(f"{cmd} (rejected: substring '{arg}')")
    # Filtre B : exécutables suspects (exact match)
    if lower_arg in DANGEROUS_EXECUTABLE_NAMES or lower_arg.endswith('/sh'):
        raise ForbiddenCommandError(f"{cmd} (rejected: executable '{arg}')")
```

**Justification :**
- `sh` n'apparaît jamais comme argument standalone dans une commande légitime whitelistée — c'est toujours un chemin (`/bin/sh`) ou unappel direct, jamais un argument comme `--show`.
- En séparant la logique, on préserve une sécurité maximale tout en éliminant les faux positifs.
- Les opérateurs de contrôle (`;`, `&&`, `|`, `-exec`) restent interceptés par sous-chaîne — c'est correct et nécessaire.

**Contexte technique :**
- Collecteurs affectés : `dpkg-query -W --showformat='${Status}\n'` (contient `--show` → `sh` substring) et autres utilisant des arguments avec des sous-chaînes commonnes.
- Faux positifs éliminés : `--show`, `dash`, `bash` (dans un chemin), `push`, etc.



---

## Décision #23

| Champ | Valeur |
|---|---|
| **Date** | 2026-07-16 |
| **Sujet** | Format du timestamp dans les manifests |
| **Statut** | **ACTIVE** |

Le schema JSON de `manifest.json` et de chaque collecteur exige un timestamp au format strict :

```
^d{4}-d{2}-d{2}Td{2}:d{2}:d{2}Z$
```

Cela signifie **sans microsecondes** (pas de `.fff` apres les secondes). Si `datetime.now().isoformat()` produit un timestamp avec microsecondes, il faut le tronquer :

```python
# Incorrect (inclut les microsecondes)
timestamp_utc = datetime.now().isoformat()

# Correct (tronque a la seconde)
timestamp_utc = datetime.now().replace(microsecond=0).isoformat() + "Z"
```

Ce probleme affectait `_write_knowledge_json` dans `core/pipeline.py`.

**Justification :** Le format ISO 8601 avec `Z` suffixe est la norme du projet. Les microsecondes ne sont pas authorisees par les schemas Pydantic et causent une erreur de validation `ValidationError`.

**Contexte technique :** La fonction `compute_json_hash` et la serialisation JSON doivent utiliser un timestamp canonique sans microsecondes pour garantir la reproductibilite du hash.

---

## Decision #24

| Champ | Valeur |
|---|---|
| **Date** | 2026-07-16 |
| **Sujet** | Prefixe `sha256:` dans le calcul du hash de document |
| **Statut** | **ACTIVE** |

La fonction `compute_json_hash` (dans `core/hashing.py` ou `core/system_adapter.py`) **ajoute deja d'elle-meme** le prefixe `sha256:` au hash hexadecimal. En faire l'ajout manuellement dans `_phase_normalize` cree un **double prefixe** `sha256:sha256:` invalide.

```python
# Incorrect (double prefixe)
doc_hash = f"sha256:{compute_json_hash(normalized_doc)}"

# Correct (compute_json_hash inclut deja le prefixe)
doc_hash = compute_json_hash(normalized_doc)
```

Ce probleme affectait `_phase_normalize` dans `core/pipeline.py`.

**Justification :** Le schema exige `^sha256:[0-9a-f]{64}$`. Si `compute_json_hash` retourne deja `"sha256:048e6a4f..."`, ajouter le prefixe produit un hash invalide qui fait echouer la validation Pydantic.

**Contexte technique :** La fonction de hash doit etre appelee **une seule fois** sur le document normalise (apres suppression du champ `hash` s'il existe deja). Le hash resultant contient deja le prefixe canonique.

---

## Decision #25

| Champ | Valeur |
|---|---|
| **Date** | 2026-07-16 |
| **Sujet** | Elimination de `Severity.ERROR` dans `_phase_compare` |
| **Statut** | **ACTIVE** |

La classe `Severity` definie dans `core/base_collector.py` ne possede **que trois niveaux** :

- `Severity.CRITICAL`
- `Severity.WARNING`
- `Severity.INFO`

Il n'existe pas de niveau `Severity.ERROR`. La ligne suivante dans `core/pipeline.py` a l'interieur de `_phase_compare` cause une `AttributeError` :

```python
# Erreur — Severity n'a pas d'attribut ERROR
elif change.severity == Severity.ERROR:

# Correction — utiliser WARNING pour les changements de niveau erreur
elif change.severity == Severity.WARNING:
```

Ou simplement supprimer ce bloc conditionnel si le comportement n'est pas souhaite.

**Justification :** L'utilisation d'un attribut inexistant sur une classe Enum provoque une exception `AttributeError: type object 'Severity' has no attribute 'ERROR'` qui interrompt le pipeline.

**Contexte technique :** Les changements detectes par `_phase_compare` sont classes dans les niveaux CRITICAL, WARNING ou INFO. Aucun changement ne devrait etre etiquette ERROR dans le cadre du projet AICollector.

---

## Decision #26

| Champ | Valeur |
|---|---|
| **Date** | 2026-07-16 |
| **Sujet** | Ordre de construction du document normalise dans `_phase_normalize` |
| **Statut** | **ACTIVE** |

Le hash d'un document doit etre calcule sur la **representation canonique** du document, c'est-a-dire **apres** que les champs variables (`timestamp_utc`, `hash`) aient ete normalises mais **avant** que le champ `hash` soit ajoute au document. L'ordre suivant est obligatoire :

```python
# 1. Nettoyer le document brut
cleaned = _normalize_doc(doc)

# 2. Supprimer le champ 'hash' s'il existe (sinon il pollue le hash)
if "hash" in cleaned:
    del cleaned["hash"]

# 3. Uniformiser le timestamp (sans microsecondes)
cleaned["timestamp_utc"] = timestamp_utc

# 4. Calculer le hash sur le document nettoye
doc_hash = compute_json_hash(cleaned)

# 5. Ajouter le champ hash au document final
cleaned["hash"] = doc_hash
```

**Justification :** Si le champ `hash` est inclus dans le document lors du calcul du hash, le hash resultant depend du hash lui-meme (dependance circulaire). Le hash doit etre calcule sur un document denue de toute metadonnee de hash.

**Contexte technique :** Cette sequence est la seule qui garantisse un hash stable et reproductible conforme a la specification.

---

## Decision #27

| Champ | Valeur |
|---|---|
| **Date** | 2026-07-16 |
| **Sujet** | Suppression du prefixe `rootfs_` dans le nom du collecteur disk_usage |
| **Statut** | **ACTIVE** |

Le collecteur de l'utilisation disque a ete initialement concu sous le nom `rootfs_disk_usage` puis simplifie en `disk_usage`. Toutes les references au collecteur dans le code et la documentation doivent utiliser le nom canonique **sans prefixe** :

- Nom du collecteur : `disk_usage`
- Chemin du schema : `schemas/disk_usage.json`
- Repertoire d'historique : `history/disk_usage/`
- Cle dans `manifest.json` : `disk_usage`

```python
# Incorrect
collector_name = "rootfs_disk_usage"

# Correct
collector_name = "disk_usage"
```

**Justification :** La coherence du nommage est essentielle pour que `_write_knowledge_json` et `_load_previous_state` puissent localiser les bons fichiers de schema et d'historique. Un nom incorrect provoque une `FileNotFoundError`.

**Contexte technique :** Le nom du collecteur est derive du nom de la classe Python `DiskUsageCollector` par conversion en snake_case (`disk_usage`). Tout prefixe ajoute manuellement doit etre evite.

---

## Decision #28

| Champ | Valeur |
|---|---|
| **Date** | 2026-07-16 |
| **Sujet** | Creation automatique des repertoires de l'arborescence FHS |
| **Statut** | **ACTIVE** |

Le pipeline doit garantir que les repertoires de l'arborescence existent avant d'y ecrire des fichiers. Les repertoires suivants doivent etre crees au besoin :

- `knowledge/` (repertoire racine de la base de connaissances)
- `knowledge/<collector>/` (un sous-repertoire par collecteur)
- `history/` (repertoire racine de l'historique)
- `history/<collector>/` (un sous-repertoire par collecteur pour les snapshots)

```python
import os

def _ensure_dir(path: str) -> None:
    """Cree le repertoire s'il n'existe pas (y compris les parents)."""
    os.makedirs(path, exist_ok=True)
```

```python
# Repertoires a creer avant l'ecriture
_ensure_dir(os.path.join(self.knowledge_dir, collector_name))
_ensure_dir(os.path.join(self.history_dir, collector_name))
```

**Justification :** Si `knowledge/` ou `history/<collector>/` n'existent pas au premier lancement, `_write_knowledge_json` echoue avec `FileNotFoundError`. La creation proactive garantit un fonctionnement nominal des l'initialisation.

**Contexte technique :** `os.makedirs(path, exist_ok=True)` est idempotent : appeler cette fonction sur un repertoire deja existant ne produit aucune erreur. Cette approche est preferee a `os.mkdir` qui echoue si le repertoire existe deja.


## Decision #29

| Champ | Valeur |
|---|---|
| **Date** | 2026-07-16 |
| **Sujet** | Gestion résiliente et sécurisée de l'UUID du serveur |
| **Statut** | **ACTIVE** |

**Architecture d'UUID serveur résilient avec auto-guérison :**

L'UUID du serveur est géré par `SystemAdapter.get_server_uuid()` (méthode statique) et persisté dans le fichier `/var/lib/aicollector/.aicollector_uuid` avec les permissions `0o600` (lecture/écriture owner only). Le Pipeline récupère cet UUID via `SystemAdapter.get_server_uuid()` lors de la Phase 0 (Initialize Metadata) et le propage dans tous les documents normalisés via l'attribut d'instance `self._server_uuid`.

**Justification :**

- **Fiabilité** : Le fichier UUID est persisté sur le disque — il survit aux redémarrages et aux mises à jour du programme.
- **Auto-guérison** : Si le fichier est absent, corrompu, ou contient un UUID invalide, la méthode le régénère automatiquement et l'écrit sur le disque, puis poursuit l'exécution.
- **Sécurité** : Les permissions `0o600` empêchent tout utilisateur autre que le owner de lire l'UUID, protégeant ainsi l'identité du serveur contre les accès non autorisés.
- **Pipeline résilient** : Le pipeline exécute la vérification de l'UUID en Phase 0 (Initialize Metadata) avant toute collecte. Si la récupération/génération échoue, le run est interrompu avec un message d'erreur explicite.
- **Intégration avec `install.sh`** : Le script d'installation génère déjà un UUID valide dans le fichier. Cette méthode le lit simplement sans rien modifier — aucune régression.

**Contexte technique :**

```python
# Emplacement du fichier UUID
UUID_FILE_PATH = "/var/lib/aicollector/.aicollector_uuid"
# Mode dev : redirection vers le répertoire de développement
if dev_root:
    uuid_file = dev_root / ".aicollector_uuid"
# Permissions sécurisées : owner only
os.chmod(uuid_file, 0o600)
```

**Flux dans le pipeline :**

```
Phase 0 — INITIALIZE METADATA :
  → SystemAdapter.get_server_uuid()
     → Lit /var/lib/aicollector/.aicollector_uuid
     → Valide le format UUIDv4
     → Si absent ou invalide : génère, écrit avec permissions 0o600
  → Stocke self._server_uuid pour propagation
  → Passe à Phase 1 COLLECT
```

**Nouvelle entrée dans `core/system_adapter.py` :**

```python
@staticmethod
def get_server_uuid() -> str:
    """Récupère ou génère l'UUID du serveur de façon résiliente."""
    # Lecture du fichier, validation UUIDv4, auto-guérison
    # Retourne un UUIDv4 valide (36 caractères hex avec tirets)
```

**Impact :**
- Les documents JSON normalisés incluent systématiquement un `server_uuid` valide.
- La configuration `config.yaml` peut contenir `server_uuid: null` — le pipeline l'ignore et utilise la valeur résiliente.
- Aucune modification des collecteurs existants requise.

---

*Derniere mise a jour : 2026-07-16*
