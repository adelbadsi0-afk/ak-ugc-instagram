"""Publie le carrousel Instagram du jour sur @ak.ugcagency et répond aux commentaires « mot-clé ».

API officielle et gratuite de Meta (Instagram API with Instagram Login, graph.instagram.com).
Lit legendes-instagram.csv + visuels/jour-XX/slide-*.jpg. Meta va chercher les images à une adresse
publique : celle du dépôt GitHub public (VISUELS_URL).

Les clés viennent des variables d'environnement : fichier .env en local, Secrets chiffrés sur GitHub.

Commandes :
    python publier_instagram.py jeton                 # colle le jeton Instagram (masqué) -> .env
    python publier_instagram.py secrets-github        # copie le jeton de .env vers les Secrets GitHub (via gh)
    python publier_instagram.py renouveler            # prolonge le jeton de 60 jours (.env + Secrets GitHub)
    python publier_instagram.py simuler [--jour N]    # affiche ce qui serait publié, n'envoie rien
    python publier_instagram.py publier [--jour N]    # publie (sans --jour : le jour de la date de Paris)
    python publier_instagram.py repondre [--essai]    # répond aux commentaires mot-clé (--essai : n'envoie rien)
    python publier_instagram.py statut
"""
import argparse
import csv
import json
import os
import random
import re
import subprocess
import sys
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import requests

try:
    from dotenv import load_dotenv
except ImportError:  # sur GitHub Actions, les variables viennent des Secrets
    load_dotenv = None

ICI = Path(__file__).parent
ENV = ICI / ".env"
CSV = ICI / "legendes-instagram.csv"
JOURNAL = ICI / "publications-instagram.json"
PARIS = ZoneInfo("Europe/Paris")

API = "https://graph.instagram.com/v24.0"
DEPOT_PUBLIC = "adelbadsi0-afk/ak-ugc-instagram"
VISUELS_URL = f"https://raw.githubusercontent.com/{DEPOT_PUBLIC}/main/visuels"
JOURS_REPONSES = 7   # Meta n'autorise la réponse privée que dans les 7 jours suivant le commentaire
REPONSES_PUBLIQUES = [
    "C'est dans tes messages 💌",
    "Envoyé en message ✨",
    "Regarde tes messages 📩 (ou tes demandes de message)",
    "C'est parti dans tes messages 💌",
]

if load_dotenv and ENV.exists():
    load_dotenv(ENV)


def log(msg):
    print(f"{datetime.now(PARIS):%Y-%m-%d %H:%M:%S}  {msg}", flush=True)


def env(nom, obligatoire=True):
    v = os.environ.get(nom, "").strip()
    if obligatoire and not v:
        sys.exit(f"Variable {nom} manquante (dans .env en local, ou dans les Secrets GitHub).")
    return v


def ecrire_env(valeurs):
    """Met à jour .env sans toucher aux autres lignes."""
    lignes = ENV.read_text(encoding="utf-8").splitlines() if ENV.exists() else []
    vus = set()
    for i, l in enumerate(lignes):
        cle = l.split("=", 1)[0].strip()
        if cle in valeurs:
            lignes[i] = f"{cle}={valeurs[cle]}"
            vus.add(cle)
    lignes += [f"{k}={v}" for k, v in valeurs.items() if k not in vus]
    ENV.write_text("\n".join(lignes) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ données
def lire_csv():
    jours = {}
    with open(CSV, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f, delimiter=";"):
            jours[int(r["jour"])] = {
                "date": datetime.strptime(r["date de publication"], "%d/%m/%Y %H:%M").replace(tzinfo=PARIS),
                "legende": r["légende"],
                "commentaire": r["premier commentaire"].strip(),
                "mot_cle": r["mot-clé"].strip(),
                "message": r["message privé"].strip(),
            }
    return jours


def jour_d_aujourdhui(jours):
    today = datetime.now(PARIS).date()
    for n, j in jours.items():
        if j["date"].date() == today:
            return n
    return None


def journal():
    return json.loads(JOURNAL.read_text(encoding="utf-8")) if JOURNAL.exists() else {}


def noter(num, infos):
    j = journal()
    j[str(num)] = infos
    JOURNAL.write_text(json.dumps(j, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")


# ------------------------------------------------------------------ API
def jeton():
    exp = env("INSTAGRAM_TOKEN_EXPIRES", obligatoire=False)
    if exp:
        reste = (datetime.fromisoformat(exp) - datetime.now(PARIS)).days
        if reste < 0:
            sys.exit("Le jeton Instagram a expiré : génère-en un nouveau (voir GUIDE-INSTAGRAM.md).")
        if reste < 10:
            log(f"ATTENTION : le jeton Instagram expire dans {reste} jours (python publier_instagram.py renouveler).")
    return env("INSTAGRAM_ACCESS_TOKEN")


def appel(methode, chemin, quoi, **params):
    """Appel à l'API ; le jeton passe dans l'en-tête (jamais dans l'adresse, ni dans les journaux)."""
    r = requests.request(methode, f"{API}/{chemin}", headers={"Authorization": f"Bearer {jeton()}"},
                         timeout=60, **params)
    if r.status_code >= 300:
        try:
            err = r.json().get("error", {})
            detail = f"{err.get('code')} / {err.get('error_subcode')} : {err.get('message')}"
        except ValueError:
            detail = r.text[:300]
        raise RuntimeError(f"{quoi} : erreur {r.status_code} ({detail})")
    return r.json()


def attendre_conteneur(cid, quoi):
    for _ in range(30):
        s = appel("GET", cid, quoi, params={"fields": "status_code,status"})
        if s.get("status_code") == "FINISHED":
            return
        if s.get("status_code") in ("ERROR", "EXPIRED"):
            raise RuntimeError(f"{quoi} : {s.get('status_code')} {s.get('status', '')}")
        time.sleep(5)
    raise RuntimeError(f"{quoi} : toujours en cours après 2 min 30")


def attendre_heure_prevue(max_minutes=45):
    """GitHub démarre les tâches planifiées avec du retard ; on les déclenche donc plus tôt
    et on attend ici l'heure prévue dans legendes-instagram.csv (18h30 Paris)."""
    jours = lire_csv()
    num = jour_d_aujourdhui(jours)
    if num is None or str(num) in journal():
        return
    reste = (jours[num]["date"] - datetime.now(PARIS)).total_seconds()
    if reste <= 0:
        return
    if reste > max_minutes * 60:
        log(f"Trop tôt ({reste / 60:.0f} min avant {jours[num]['date']:%H:%M}) : un déclenchement suivant s'en chargera.")
        sys.exit(0)
    log(f"Attente de {reste / 60:.0f} min pour publier à {jours[num]['date']:%H:%M} pile.")
    time.sleep(reste)


def images(num):
    imgs = sorted((ICI / "visuels" / f"jour-{num:02d}").glob("slide-*.jpg"))
    if not 2 <= len(imgs) <= 20:
        sys.exit(f"Jour {num} : {len(imgs)} images trouvées (Instagram en demande entre 2 et 20).")
    base = env("VISUELS_URL", obligatoire=False) or VISUELS_URL
    return [f"{base}/jour-{num:02d}/{p.name}" for p in imgs]


def publier(num, simuler=False):
    jours = lire_csv()
    if num is None:
        log("Pas de post prévu aujourd'hui.")
        return
    if num not in jours:
        sys.exit(f"Jour {num} absent de legendes-instagram.csv")
    if str(num) in journal() and not simuler:
        log(f"Jour {num} déjà publié : {journal()[str(num)]['url']}")
        return
    j = jours[num]
    urls = images(num)

    if simuler:
        print(f"--- Jour {num} ({j['date']:%d/%m/%Y %H:%M}) : {len(urls)} images, ex. {urls[0]}")
        print(j["legende"])
        print(f"--- Premier commentaire : {j['commentaire'] or '(aucun)'}")
        print(f"--- Mot-clé : {j['mot_cle'] or '(aucun)'}\n--- Message privé :\n{j['message']}")
        return

    # Meta doit pouvoir lire chaque image à son adresse publique
    for u in urls:
        r = requests.head(u, timeout=30, allow_redirects=True)
        if r.status_code != 200:
            sys.exit(f"Image inaccessible ({r.status_code}) : {u} — le dépôt public est-il à jour ?")

    ig = env("INSTAGRAM_USER_ID")
    log(f"Jour {num} : préparation de {len(urls)} images")
    enfants = []
    for k, u in enumerate(urls, 1):
        c = appel("POST", f"{ig}/media", f"Image {k}", data={"image_url": u, "is_carousel_item": "true"})
        enfants.append(c["id"])
    for k, c in enumerate(enfants, 1):
        attendre_conteneur(c, f"Image {k}")
    carrousel = appel("POST", f"{ig}/media", "Carrousel",
                      data={"media_type": "CAROUSEL", "children": ",".join(enfants), "caption": j["legende"]})["id"]
    attendre_conteneur(carrousel, "Carrousel")
    media = appel("POST", f"{ig}/media_publish", "Publication", data={"creation_id": carrousel})["id"]
    lien = appel("GET", media, "Lien du post", params={"fields": "permalink"}).get("permalink", "")
    infos = {"date": datetime.now(PARIS).isoformat(timespec="seconds"), "media": media, "url": lien,
             "mot_cle": j["mot_cle"]}
    noter(num, infos)
    log(f"Jour {num} publié : {lien}")

    if j["commentaire"]:
        time.sleep(15)
        try:
            appel("POST", f"{media}/comments", "Premier commentaire", data={"message": j["commentaire"]})
            infos["premier_commentaire"] = True
            log("Premier commentaire posté")
        except RuntimeError as e:
            infos["premier_commentaire"] = False
            log(f"Premier commentaire NON posté, à faire à la main : {e}")
        noter(num, infos)


# ------------------------------------------------------------------ réponses automatiques
def simplifier(t):
    t = unicodedata.normalize("NFD", t or "").encode("ascii", "ignore").decode().upper()
    return re.sub(r"[^A-Z0-9]+", " ", t)


def contient_mot_cle(texte, mot):
    return f" {simplifier(mot).strip()} " in f" {simplifier(texte)} "


def tous(chemin, quoi, params):
    """Lit toutes les pages d'une liste de l'API."""
    out, page = [], appel("GET", chemin, quoi, params=params)
    while True:
        out += page.get("data", [])
        suivant = page.get("paging", {}).get("next")
        if not suivant:
            return out
        r = requests.get(suivant, headers={"Authorization": f"Bearer {jeton()}"}, timeout=60)
        if not r.ok:
            return out
        page = r.json()


def repondre(essai=False):
    ig = env("INSTAGRAM_USER_ID")
    moi = appel("GET", "me", "Compte", params={"fields": "username"}).get("username", "")
    messages = {str(n): j for n, j in lire_csv().items()}
    limite = datetime.now(PARIS) - timedelta(days=JOURS_REPONSES)
    envoyes = 0
    for num, pub in journal().items():
        j = messages.get(num)
        if not j or not j["mot_cle"] or not j["message"] or not pub.get("media"):
            continue
        if datetime.fromisoformat(pub["date"]) < limite:
            continue
        coms = tous(f"{pub['media']}/comments", f"Commentaires jour {num}",
                    {"fields": "id,text,timestamp,username,replies{username}", "limit": 50})
        for c in coms:
            if c.get("username") == moi or not contient_mot_cle(c.get("text"), j["mot_cle"]):
                continue
            if datetime.fromisoformat(c["timestamp"].replace("+0000", "+00:00")) < limite:
                continue
            if any(r.get("username") == moi for r in c.get("replies", {}).get("data", [])):
                continue  # déjà traité (notre réponse publique sert de marque)
            if essai:
                log(f"[essai] jour {num} : enverrait le message à un commentaire « {j['mot_cle']} »")
                continue
            try:
                appel("POST", f"{ig}/messages", "Message privé",
                      json={"recipient": {"comment_id": c["id"]}, "message": {"text": j["message"]}})
                appel("POST", f"{c['id']}/replies", "Réponse publique",
                      data={"message": random.choice(REPONSES_PUBLIQUES)})
                envoyes += 1
                log(f"Jour {num} : message « {j['mot_cle']} » envoyé")
                time.sleep(3)
            except RuntimeError as e:
                log(f"Jour {num} : réponse impossible pour un commentaire : {e}")
    log(f"Réponses automatiques : {envoyes} message(s) envoyé(s).")


# ------------------------------------------------------------------ jeton (en local)
def saisir_jeton():
    """Demande le jeton (collage masqué), le vérifie et l'écrit dans .env, sans jamais l'afficher."""
    import getpass
    print("Colle le jeton Instagram (clic droit ou Ctrl+V), puis Entrée. Rien ne s'affiche, c'est normal.")
    v = getpass.getpass("Jeton : ").strip()
    if len(v) < 50 or " " in v:
        sys.exit("Ça ne ressemble pas à un jeton Instagram. Recommence.")
    os.environ["INSTAGRAM_ACCESS_TOKEN"] = v
    os.environ.pop("INSTAGRAM_TOKEN_EXPIRES", None)
    info = appel("GET", "me", "Vérification du jeton", params={"fields": "user_id,username"})
    expire = datetime.now(PARIS) + timedelta(days=59)
    ecrire_env({"INSTAGRAM_ACCESS_TOKEN": v, "INSTAGRAM_USER_ID": info["user_id"],
                "INSTAGRAM_TOKEN_EXPIRES": expire.isoformat(timespec="seconds")})
    print(f"Connecté au compte @{info['username']}. Jeton enregistré dans .env (valable jusqu'au {expire:%d/%m/%Y} environ).")


def renouveler():
    r = requests.get("https://graph.instagram.com/refresh_access_token",
                     params={"grant_type": "ig_refresh_token", "access_token": jeton()}, timeout=30)
    if not r.ok:
        sys.exit(f"Renouvellement impossible : {r.status_code} {r.json().get('error', {}).get('message', '')}")
    t = r.json()
    expire = datetime.now(PARIS) + timedelta(seconds=int(t.get("expires_in", 0)))
    os.environ["INSTAGRAM_ACCESS_TOKEN"] = t["access_token"]
    ecrire_env({"INSTAGRAM_ACCESS_TOKEN": t["access_token"], "INSTAGRAM_TOKEN_EXPIRES": expire.isoformat(timespec="seconds")})
    log(f"Jeton prolongé jusqu'au {expire:%d/%m/%Y}.")
    secrets_github()


def secrets_github():
    gh = os.environ.get("GH_EXE", "gh")
    for cle in ("INSTAGRAM_ACCESS_TOKEN", "INSTAGRAM_USER_ID", "INSTAGRAM_TOKEN_EXPIRES"):
        # valeur passée par l'entrée standard : jamais affichée ni visible dans la ligne de commande
        subprocess.run([gh, "secret", "set", cle, "--repo", DEPOT_PUBLIC], input=env(cle), text=True, check=True)
        log(f"Secret GitHub {cle} mis à jour")


def statut():
    exp = env("INSTAGRAM_TOKEN_EXPIRES", obligatoire=False)
    print("Jeton Instagram :", f"valable jusqu'au {exp[:10]}" if exp else "absent")
    print("Aujourd'hui = jour", jour_d_aujourdhui(lire_csv()))
    for k, v in sorted(journal().items(), key=lambda x: int(x[0])):
        print(f"  jour {int(k):02d} publié le {v['date']} -> {v['url']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("action", choices=["jeton", "secrets-github", "renouveler", "simuler", "publier", "repondre", "statut"])
    ap.add_argument("--jour", type=int)
    ap.add_argument("--attendre", action="store_true",
                    help="(GitHub) si on est avant l'heure prévue, attendre jusqu'à l'heure exacte (45 min max)")
    ap.add_argument("--essai", action="store_true", help="repondre : affiche sans rien envoyer")
    a = ap.parse_args()
    if a.action == "jeton":
        saisir_jeton()
    elif a.action == "secrets-github":
        secrets_github()
    elif a.action == "renouveler":
        renouveler()
    elif a.action == "statut":
        statut()
    elif a.action == "repondre":
        repondre(essai=a.essai)
    else:
        if a.action == "publier" and a.attendre and not a.jour:
            attendre_heure_prevue()
        publier(a.jour or jour_d_aujourdhui(lire_csv()), simuler=a.action == "simuler")
