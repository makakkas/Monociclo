# Lo corro desde PowerShell cuando quiero sacar otra versión del exe.
# Uso un entorno aparte para no mezclar mis librerías con las de otros proyectos.
$ErrorActionPreference = 'Stop'
$MonocicloCarpeta = $PSScriptRoot
$MonocicloPython = Join-Path $MonocicloCarpeta '.venv\Scripts\python.exe'
if (-not (Test-Path -LiteralPath $MonocicloPython)) {
    py -m venv (Join-Path $MonocicloCarpeta '.venv')
    if ($LASTEXITCODE -ne 0) { throw 'No pude crear .venv. Revisa que Python esté instalado.' }
}
& $MonocicloPython -m pip install -r (Join-Path $MonocicloCarpeta 'requirements.txt')
if ($LASTEXITCODE -ne 0) { throw 'Falló la instalación de dependencias.' }

# Qt necesita sus DLL correctas. Un PATH lleno de herramientas puede meter otra
# versión de ICU en el paquete. Cambio PATH sólo aquí y después lo dejo como estaba.
$MonocicloPathAnterior = $env:PATH
try {
    $env:PATH = ((Split-Path $MonocicloPython) + ';' + $env:SystemRoot + '\System32;' + $env:SystemRoot)
    & $MonocicloPython -m PyInstaller --clean --onefile --windowed --name Monociclo `
        --distpath $MonocicloCarpeta `
        --workpath (Join-Path $MonocicloCarpeta 'build') `
        --specpath $MonocicloCarpeta `
        (Join-Path $MonocicloCarpeta 'focus_guard.py')
    if ($LASTEXITCODE -ne 0) { throw 'La compilación no terminó bien.' }
} finally {
    $env:PATH = $MonocicloPathAnterior
}
Write-Host 'Listo: Monociclo.exe está junto al código y al JSON.'
