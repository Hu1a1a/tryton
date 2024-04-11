# Estricto manual de como se realiza el montaje del Tryton BC
## general:

```console
python -m venv venv
call .\venv\Scripts\activate.bat

python .hooks/link_modules
**cmd con administrador**

pip install -e trytond -e tryton -e proteus
pip install -r requirements.txt -r requirements-dev.txt
```

## sao:

```console
npm i --legacy-peer-deps
npm i -g grunt-cli

### for dev
grunt dev 
### for production
grunt default
use just grunt
```

## trytond:

```console
pip install .
python bin/trytond-admin -c trytond.conf -d tryton -p
python bin/trytond-admin -c trytond.conf -d tryton --all
python bin/trytond -c trytond.conf
```

### delete module:
- marketing_campaign
- authentication_saml
- account_statement_coda


## module change
- delete .\sao\node_modules\po2json\package.json -> main:""
- change moment.js updaterlocale

# Fichero modificado
```
requirements.txt
requirements-dev.txt
trytond/trytond.conf
sao/custom.css
sao/custom.js
```

## tryton client:

```
Run mysys64
For the developing use python bin/tryton
For the build use make-win32-installler.sh and will get tryton.exe at the same folder
```
