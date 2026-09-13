Add-Type -AssemblyName System.Drawing

$dir = "test-results\browser\corner-clearance"
$files = Get-ChildItem -LiteralPath $dir -Filter *.png | Sort-Object Name

foreach ($f in $files) {
  $bmp = [System.Drawing.Bitmap]::FromFile($f.FullName)
  $w = $bmp.Width; $h = $bmp.Height

  # orb in the true corner
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
  # label
  $sMinY = $h; $sMaxY = -1
  for ($y = [Math]::Max(0, $orbMinY - 50); $y -lt $orbMinY; $y++) {
    for ($x = [Math]::Max(0, $orbCX - 75); $x -le [Math]::Min($w - 1, $orbCX + 75); $x++) {
      $c = $bmp.GetPixel($x, $y)
      if ([Math]::Abs($c.R - 14) -lt 45 -and [Math]::Abs($c.G - 120) -lt 50 -and [Math]::Abs($c.B - 175) -lt 50 -and ($c.B - $c.R) -gt 60) {
        if ($y -lt $sMinY) { $sMinY = $y }; if ($y -gt $sMaxY) { $sMaxY = $y }
      }
    }
  }

  # ink = dark-ish pixel (text, border, icon). canvas is ~#f5f8fb (245,248,251), cards white.
  function Count-Ink($bmp, $x0, $x1, $y0, $y1) {
    $n = 0; $minX = 99999; $maxX = -1; $minY = 99999; $maxY = -1
    for ($y = [Math]::Max(0, $y0); $y -le [Math]::Min($bmp.Height - 1, $y1); $y++) {
      for ($x = [Math]::Max(0, $x0); $x -le [Math]::Min($bmp.Width - 1, $x1); $x++) {
        $c = $bmp.GetPixel($x, $y)
        $mx = [Math]::Max($c.R, [Math]::Max($c.G, $c.B))
        if ($mx -lt 170) {
          $n++
          if ($x -lt $minX) { $minX = $x }; if ($x -gt $maxX) { $maxX = $x }
          if ($y -lt $minY) { $minY = $y }; if ($y -gt $maxY) { $maxY = $y }
        }
      }
    }
    return @($n, $minX, $maxX, $minY, $maxY)
  }

  # left strip beside the whole widget column
  $l = Count-Ink $bmp ($orbMinX - 30) ($orbMinX - 1) $sMinY $orbMaxY
  # strip below the widget (should be empty / bottom edge)
  $b = Count-Ink $bmp ($orbMinX - 30) ($w - 1) ($orbMaxY + 1) ($h - 1)

  Write-Output ("{0} | widgetX={1}..{2} y={3}..{4} | LEFTstrip ink={5} bbox=({6},{7})-({8},{9}) | BELOWstrip ink={10} bbox=({11},{12})-({13},{14})" -f `
      $f.Name, $orbMinX, $orbMaxX, $sMinY, $orbMaxY, $l[0], $l[1], $l[3], $l[2], $l[4], $b[0], $b[1], $b[3], $b[2], $b[4])
  $bmp.Dispose()
}
