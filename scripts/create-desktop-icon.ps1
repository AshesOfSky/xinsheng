$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Drawing
$taskAssets = Join-Path (Split-Path -Parent $PSScriptRoot) 'desktop\Assets'
New-Item -ItemType Directory -Path $taskAssets -Force | Out-Null
$taskImages = @()
foreach ($taskSize in @(16, 24, 32, 48, 64, 128, 256)) {
    $taskBitmap = [Drawing.Bitmap]::new($taskSize, $taskSize, [Drawing.Imaging.PixelFormat]::Format32bppArgb)
    $taskGraphics = [Drawing.Graphics]::FromImage($taskBitmap)
    $taskGraphics.SmoothingMode = [Drawing.Drawing2D.SmoothingMode]::AntiAlias
    $taskGraphics.Clear([Drawing.Color]::Transparent)
    $taskPath = [Drawing.Drawing2D.GraphicsPath]::new()
    $taskCorner = [single]($taskSize * .36)
    $taskEdge = [single]($taskSize - 1)
    $taskPath.AddArc(0, 0, $taskCorner, $taskCorner, 180, 90)
    $taskPath.AddArc(($taskEdge - $taskCorner), 0, $taskCorner, $taskCorner, 270, 90)
    $taskPath.AddArc(($taskEdge - $taskCorner), ($taskEdge - $taskCorner), $taskCorner, $taskCorner, 0, 90)
    $taskPath.AddArc(0, ($taskEdge - $taskCorner), $taskCorner, $taskCorner, 90, 90)
    $taskPath.CloseFigure()
    $taskBrush = [Drawing.SolidBrush]::new([Drawing.Color]::FromArgb(40, 90, 70))
    $taskGraphics.FillPath($taskBrush, $taskPath)
    $taskPen = [Drawing.Pen]::new([Drawing.Color]::FromArgb(255, 254, 250), [single]($taskSize * .09))
    $taskPen.StartCap = [Drawing.Drawing2D.LineCap]::Round; $taskPen.EndCap = [Drawing.Drawing2D.LineCap]::Round
    foreach ($taskBar in @(@(.29, .40, .35, .66), @(.46, .25, .55, .74), @(.65, .34, .73, .69))) {
        $taskGraphics.DrawLine($taskPen, [single]($taskBar[0] * $taskSize), [single]($taskBar[1] * $taskSize), [single]($taskBar[2] * $taskSize), [single]($taskBar[3] * $taskSize))
    }
    $taskStream = [IO.MemoryStream]::new(); $taskBitmap.Save($taskStream, [Drawing.Imaging.ImageFormat]::Png)
    $taskImages += @{ Size = $taskSize; Bytes = $taskStream.ToArray() }
    $taskStream.Dispose(); $taskPen.Dispose(); $taskBrush.Dispose(); $taskPath.Dispose(); $taskGraphics.Dispose(); $taskBitmap.Dispose()
}
$taskIcon = [IO.File]::Create((Join-Path $taskAssets 'echo.ico'))
$taskWriter = [IO.BinaryWriter]::new($taskIcon)
try {
    $taskWriter.Write([uint16]0); $taskWriter.Write([uint16]1); $taskWriter.Write([uint16]$taskImages.Count)
    $taskOffset = 6 + 16 * $taskImages.Count
    foreach ($taskImage in $taskImages) {
        $taskDimension = if ($taskImage.Size -eq 256) { 0 } else { $taskImage.Size }
        $taskWriter.Write([byte]$taskDimension); $taskWriter.Write([byte]$taskDimension); $taskWriter.Write([byte]0); $taskWriter.Write([byte]0)
        $taskWriter.Write([uint16]1); $taskWriter.Write([uint16]32); $taskWriter.Write([uint32]$taskImage.Bytes.Length); $taskWriter.Write([uint32]$taskOffset)
        $taskOffset += $taskImage.Bytes.Length
    }
    foreach ($taskImage in $taskImages) { $taskWriter.Write([byte[]]$taskImage.Bytes) }
} finally { $taskWriter.Dispose(); $taskIcon.Dispose() }
Write-Output 'Created desktop/Assets/echo.ico'
