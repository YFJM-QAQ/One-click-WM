:: 远方即明制作

@echo off
chcp 65001 >nul

setlocal EnableExtensions EnableDelayedExpansion

set "INI1=%~1"
set "INI2=%~2"
set "SECTION=ini_setting"
set "CUR="

for /f "usebackq tokens=1,* delims==" %%A in ("%INI1%") do (
    set "KEY=%%A"
    set "VAL=%%B"

    for /f "tokens=* delims= " %%K in ("!KEY!") do set "KEY=%%K"

    if not "!KEY!"=="" (
        if "!KEY:~0,1!"=="[" (
            set "CUR=!KEY:[=!"
            set "CUR=!CUR:]=!"
        ) else (
            if /i "!CUR!"=="%SECTION%" set "!KEY!=!VAL!"
        )
    )
)

for /f "usebackq tokens=1,* delims==" %%A in ("%INI2%") do (
    set "KEY=%%A"
    set "VAL=%%B"

    for /f "tokens=* delims= " %%K in ("!KEY!") do set "KEY=%%K"

    if not "!KEY!"=="" (
        if "!KEY:~0,1!"=="[" (
            set "CUR=!KEY:[=!"
            set "CUR=!CUR:]=!"
        ) else (
            if /i "!CUR!"=="%SECTION%" set "!KEY!=!VAL!"
        )
    )
)


title 批量图片水印处理工具 by  远方即明

if not exist "%INPUT_DIR%" (
    mkdir "%INPUT_DIR%"
    echo 警告:未找到目录！
    echo 已为您创建目录，请在目录放入图像后回车。
    pause >nul
) 

if not exist "%LOGO_PATH%" (
	echo 错误:LOGO不存在!
	echo 按任意键退出...
	pause >nul
	exit
)

if not defined TEXT (
	echo 警告:文本不存在!
)

if "%MASK_ENABLE%"=="OFF" (
	set "MASK_COLOR=0,0,0,0"
	echo 警告:遮挡色块未启用!
)

echo 正在启动图片处理工具，请稍候...
python ".\bin\main.py" "%INPUT_DIR%" -o "%OUTPUT_DIR%" -j 15 -t "%TEXT%"  --font "%FONT_PATH%" --font-size %FONT_SIZE% --text-color %TEXT_COLOR% --stroke-color %STROKE_COLOR% --stroke-width %STROKE_WIDTH% --box-color 0,0,0,0 --anchor %ANCHOR% --offset %OFFSET% --mask %MASK% --mask-color %MASK_COLOR% --logo "%LOGO_PATH%" --logo-scale %LOGO_SCALE% --logo-opacity %LOGO_OPACITY% --quality --subsampling

echo.
echo 处理完成！按任意键退出...
pause >nul
