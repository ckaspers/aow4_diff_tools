@echo off
rem After a new DUMPALL dump is copied into the website: its texts, unit categories, city tree and magic materials, from the
rem live game with the mod. In this order: alltexts.py leaves the abilities abilities.py writes out of the concept
rem tooltips, and magicmaterials.py words its links the way all.json does.
cd /d "%~dp0"
set PY=python
where py >nul 2>nul && set PY=py -3
%PY% abilities.py || goto failed
%PY% alltexts.py || goto failed
%PY% spawnsets.py || goto failed
%PY% tmlicons.py || goto failed
%PY% realmtraits.py || goto failed
%PY% citytree.py || goto failed
%PY% magicmaterials.py || goto failed
echo.
echo Done: look over the changes in the website's folder before committing them.
pause
exit /b 0
:failed
echo.
echo Stopped: the step above failed, the ones after it did not run.
pause
exit /b 1
