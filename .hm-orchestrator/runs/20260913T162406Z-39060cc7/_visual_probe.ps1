Add-Type -AssemblyName System.Drawing

$dir = "test-results\browser\corner-clearance"
$files = Get-ChildItem -LiteralPath $dir -Filter *.png | Sort-Object Name

foreach ($f in $files) {
  $bmp = [System.Drawing.Bitmap]::FromFile($f.FullName)
  $w = $bmp.Width; $h = $bmp.Height
  $x0 = [Math]::Max(0, $w - 260)
  $y0 = [Math]::Max(0, $h - 320)

  $skyMinX = $w; $skyMaxX = -1; $skyMinY = $h; $skyMaxY = -1; $skyCount = 0
  $orbMinX = $w; $orbMaxX = -1; $orbMinY = $h; $orbMaxY = -1; $orbCount = 0
  # white-ish text pixels inside the label box (found after sky bbox is known)
  $txtCount = 0

  for ($y = $y0; $y -lt $h; $y++) {
    for ($x = $x0; $x -lt $w; $x++) {
      $c = $bmp.GetPixel($x, $y)
      $r = $c.R; $g = $c.G; $b = $c.B
      if ([Math]::Abs($r - 14) -lt 45 -and [Math]::Abs($g - 120) -lt 50 -and [Math]::Abs($b - 175) -lt 50 -and ($b - $r) -gt 60) {
        $skyCount++
        if ($x -lt $skyMinX) { $skyMinX = $x }; if ($x -gt $skyMaxX) { $skyMaxX = $x }
        if ($y -lt $skyMinY) { $skyMinY = $y }; if ($y -gt $skyMaxY) { $skyMaxY = $y }
      }
      if ($g -gt 190 -and $r -gt 120 -and $r -lt 235 -and $b -lt 160 -and ($g - $b) -gt 60) {
        $orbCount++
        if ($x -lt $orbMinX) { $orbMinX = $x }; if ($x -gt $orbMaxX) { $orbMaxX = $x }
        if ($y -lt $orbMinY) { $orbMinY = $y }; if ($y -gt $orbMaxY) { $orbMaxY = $y }
      }
    }
  }

  # count near-white text pixels strictly inside the sky bbox
  if ($skyMaxX -gt $skyMinX) {
    for ($y = $skyMinY; $y -le $skyMaxY; $y++) {
      for ($x = $skyMinX; $x -le $skyMaxX; $x++) {
        $c = $bmp.GetPixel($x, $y)
        if ($c.R -gt 235 -and $c.G -gt 235 -and $c.B -gt 235) { $txtCount++ }
      }
    }
  }

  $labelW = $skyMaxX - $skyMinX + 1
  $labelH = $skyMaxY - $skyMinY + 1
  $orbW = $orbMaxX - $orbMinX + 1
  $orbH = $orbMaxY - $orbMinY + 1
  $labelCX = ($skyMinX + $skyMaxX) / 2.0
  $orbCX = ($orbMinX + $orbMaxX) / 2.0
  $gap = $skyMaxY - $orbMinY

  Write-Output ("{0} | {1}x{2} | label px={3} box=({4},{5})-({6},{7}) {8}x{9} cx={10} | orb px={11} box=({12},{13})-({14},{15}) {16}x{17} cx={18} | whiteTextPx={19} | label-orb gap={20}" -f `
      $f.Name, $w, $h, $skyCount, $skyMinX, $skyMinY, $skyMaxX, $skyMaxY, $labelW, $labelH, $labelCX, `
      $orbCount, $orbMinX, $orbMinY, $orbMaxX, $orbMaxY, $orbW, $orbH, $orbCX, $txtCount, $gap)
  $bmp.Dispose()
}
