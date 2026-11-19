# AK UGC — carrousels Instagram

Publication automatique des carrousels d'[@ak.ugcagency](https://www.instagram.com/ak.ugcagency/)
avec l'API officielle d'Instagram (Instagram API with Instagram Login), via GitHub Actions.

- `visuels/` : les images des carrousels (JPEG 1080 x 1350)
- `legendes-instagram.csv` : dates, légendes, mots-clés et messages
- `publier_instagram.py` : publication + réponses aux commentaires mot-clé
- `.github/workflows/instagram.yml` : publication quotidienne à 18h30 (Paris) et réponses toutes les 15 minutes

Aucune clé n'est stockée dans ce dépôt : elles sont dans les Secrets chiffrés de GitHub.
