Add-Type -AssemblyName System.Drawing

$dir = "test-results\browser\corner-clearance"
$files = Get-ChildItem -LiteralPath $dir -Filter *.png | Sort-Object Name

foreach ($f in $files) {
  $bmp = [System.Drawing.Bitmap]::FromFile($f.FullName)
  $w = $bmp.Width; $h = $bmp.Height

  # orb: lime pixels only in the true bottom-right corner
  $orbMinX = $w; $orbMaxX = -1; $orbMinY = $h; $orbMaxY = -1
  for ($y = [Math]::Max(0, $h - 120); $y -lt $h; $y++) {
    for ($x = [Math]::Max(0, $w - 80); $x -lt $w; $x++) {
      $c = $bmp.GetPixel($x, $y)
      if ($c.G -gt 190 -and $c.R -gt 120 -and $c.R -lt 235 -and $c.B -lt 160 -and ($c.G - $c.B) -gt 60) {
        if ($x -lt $orbMinX) { $orbMinX = $x }; if ($x -gt $orbMaxX) { $orbMaxX = $x }
        if ($y -lt $orbMinY) { $orbMinY = $y }; if ($y -gt $orbMaxY) { $orbMaxY = $y }
      }
    }
  }
  $orbCX = [int](($orbMinX + $orbMaxX) / 2)

  # label: sky pixels only in the column above the orb
  $lx0 = [Math]::Max(0, $orbCX - 75); $lx1 = [Math]::Min($w - 1, $orbCX + 75)
  $ly0 = [Math]::Max(0, $orbMinY - 50); $ly1 = $orbMinY - 1
  $sMinX = $w; $sMaxX = -1; $sMinY = $h; $sMaxY = -1
  $spans = @{}
  for ($y = $ly0; $y -le $ly1; $y++) {
    $rowMin = $w; $rowMax = -1
    for ($x = $lx0; $x -le $lx1; $x++) {
      $c = $bmp.GetPixel($x, $y)
      if ([Math]::Abs($c.R - 14) -lt 45 -and [Math]::Abs($c.G - 120) -lt 50 -and [Math]::Abs($c.B - 175) -lt 50 -and ($c.B - $c.R) -gt 60) {
        if ($x -lt $rowMin) { $rowMin = $x }; if ($x -gt $rowMax) { $rowMax = $x }
        if ($x -lt $sMinX) { $sMinX = $x }; if ($x -gt $sMaxX) { $sMaxX = $x }
        if ($y -lt $sMinY) { $sMinY = $y }; if ($y -gt $sMaxY) { $sMaxY = $y }
      }
    }
    $spans[$y] = if ($rowMax -ge $rowMin) { $rowMax - $rowMin + 1 } else { 0 }
  }

  $lw = $sMaxX - $sMinX + 1; $lh = $sMaxY - $sMinY + 1
  $maxSpan = 0; foreach ($k in $spans.Keys) { if ($spans[$k] -gt $maxSpan) { $maxSpan = $spans[$k] } }
  $row1 = if ($spans.ContainsKey($sMinY + 1)) { $spans[$sMinY + 1] } else { 0 }
  $row2 = if ($spans.ContainsKey($sMinY + 2)) { $spans[$sMinY + 2] } else { 0 }
  $row3 = if ($spans.ContainsKey($sMinY + 3)) { $spans[$sMinY + 3] } else { 0 }
  $midY = $sMinY + [int]($lh / 2)
  $fill = $bmp.GetPixel($sMinX + 5, $midY)
  $fillHex = ("#{0:X2}{1:X2}{2:X2}" -f $fill.R, $fill.G, $fill.B)
  $txt = 0
  for ($y = $sMinY; $y -le $sMaxY; $y++) {
    for ($x = $sMinX; $x -le $sMaxX; $x++) {
      $c = $bmp.GetPixel($x, $y)
      if ($c.R -gt 235 -and $c.G -gt 235 -and $c.B -gt 235) { $txt++ }
    }
  }

  Write-Output ("{0} | orb {1}x{2} at ({3},{4}) cx={5} | label {6}x{7} at ({8},{9}) | spans top+1..+3={10},{11},{12} max={13} | fillLeft={14} | whiteTextPx={15} | gap={16}" -f `
      $f.Name, ($orbMaxX - $orbMinX + 1), ($orbMaxY - $orbMinY + 1), $orbMinX, $orbMinY, $orbCX, `
      $lw, $lh, $sMinX, $sMinY, $row1, $row2, $row3, $maxSpan, $fillHex, $txt, ($orbMinY - $sMaxY))
  $bmp.Dispose()
}
