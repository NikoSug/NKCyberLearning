# Contribuer à CyberLearning

Merci de votre intérêt ! Les contributions (corrections, fonctionnalités, documentation, traductions)
sont les bienvenues.

## Avant de commencer

- **Bug ou idée** : ouvrez d'abord une *issue* (gabarits fournis) pour en discuter, surtout pour une
  fonctionnalité importante.
- **Faille de sécurité** : ne l'ouvrez pas en public ; suivez [SECURITY.md](SECURITY.md).
- **Installation et règles de code** : [docs/developpement.md](docs/developpement.md) (Docker, tests,
  sécurité, style).

## Proposer une modification

1. Forkez le dépôt et créez une branche à partir de `main`.
2. Un commit par étape cohérente, message court en français, à l'impératif.
3. Ajoutez ou adaptez les tests ; tout correctif de bug vient avec le test qui le reproduit. Les tests
   doivent passer (`python manage.py test`, voir docs/developpement.md).
4. **Signez chaque commit** (`git commit -s`), voir ci-dessous.
5. Ouvrez une *pull request* vers `main` en remplissant le gabarit.

Les mainteneurs relisent la PR, puis l'intègrent à la version suivante : votre commit est alors repris
tel quel (auteur, message et signature conservés) et publié avec cette version.

## Signature et accord de contribution

Le projet garde la trace de chaque contributeur et de son accord. En signant un commit avec
`git commit -s`, vous y ajoutez la ligne :

```
Signed-off-by: Prénom Nom <adresse@exemple.fr>
```

Elle vaut **acceptation de l'accord de contribution ci-dessous** pour ce commit. Utilisez une identité
réelle (ou un pseudonyme stable) et une adresse joignable : une PR dont un commit n'est pas signé ne
peut pas être intégrée. Pour signer après coup : `git rebase --signoff main`, puis poussez la branche.

### Accord de contribution

En signant une contribution, vous déclarez et acceptez que :

1. **Origine** : la contribution est votre création, ou vous avez le droit de la soumettre (par exemple
   avec l'accord de votre employeur si elle a été réalisée dans le cadre de votre travail), et elle ne
   contient aucun élément soumis à une licence incompatible.
2. **Licence** : la contribution est diffusée sous la licence du projet,
   [CeCILL v2.1](LICENSE).
3. **Droits accordés au titulaire du projet** : vous accordez en outre au titulaire des droits du
   projet (son auteur, propriétaire du dépôt officiel), à titre
   gratuit, non exclusif, mondial et pour toute la durée des droits d'auteur, le droit d'utiliser, de
   reproduire, de modifier et de distribuer votre contribution, y compris sous d'autres conditions de
   licence que la CeCILL v2.1. Vous restez titulaire de vos droits sur votre contribution et pouvez
   l'utiliser librement par ailleurs.
4. **Traçabilité** : la contribution et votre signature (nom et adresse indiqués) sont conservées de
   façon publique et permanente dans l'historique du projet.

L'article 3 permet au titulaire de proposer le logiciel sous d'autres licences (par exemple à une
organisation qui ne peut pas accepter la CeCILL), en plus de la version libre qui reste disponible sous
CeCILL v2.1.

## Code de conduite

Restez courtois et constructifs : les échanges portent sur le code, pas sur les personnes. Les
mainteneurs peuvent modérer ou fermer une discussion qui ne respecte pas cette règle.
