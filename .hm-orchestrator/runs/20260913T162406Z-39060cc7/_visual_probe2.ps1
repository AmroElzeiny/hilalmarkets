Add-Type -AssemblyName System.Drawing

$dir = "test-results\browser\corner-clearance"
$files = Get-ChildItem -LiteralPath $dir -Filter *.png | Sort-Object Name

foreach ($f in $files) {
  $bmp = [System.Drawing.Bitmap]::FromFile($f.FullName)
  $w = $bmp.Width; $h = $bmp.Height

  # 1) orb: lime pixels anywhere in bottom-right region
  $orbMinX = $w; $orbMaxX = -1; $orbMinY = $h; $orbMaxY = -1
  for ($y = [Math]::Max(0, $h - 320); $y -lt $h; $y++) {
    for ($x = [Math]::Max(0, $w - 260); $x -lt $w; $x++) {
      $c = $bmp.GetPixel($x, $y)
      if ($c.G -gt 190 -and $c.R -gt 120 -and $c.R -lt 235 -and $c.B -lt 160 -and ($c.G - $c.B) -gt 60) {
        if ($x -lt $orbMinX) { $orbMinX = $x }; if ($x -gt $orbMaxX) { $orbMaxX = $x }
        if ($y -lt $orbMinY) { $orbMinY = $y }; if ($y -gt $orbMaxY) { $orbMaxY = $y }
      }
    }
  }
  $orbCX = [int](($orbMinX + $orbMaxX) / 2)

  # 2) label: sky pixels only in the column above the orb
  $lx0 = [Math]::Max(0, $orbCX - 75); $lx1 = [Math]::Min($w - 1, $orbCX + 75)
  $ly0 = [Math]::Max(0, $orbMinY - 50); $ly1 = $orbMinY - 1
  $sMinX = $w; $sMaxX = -1; $sMinY = $h; $sMaxY = -1
  $rows = @{}
  for ($y = $ly0; $y -le $ly1; $y++) {
    $cnt = 0
    for ($x = $lx0; $x -le $lx1; $x++) {
      $c = $bmp.GetPixel($x, $y)
      if ([Math]::Abs($c.R - 14) -lt 45 -and [Math]::Abs($c.G - 120) -lt 50 -and [Math]::Abs($c.B - 175) -lt 50 -and ($c.B - $c.R) -gt 60) {
        $cnt++
        if ($x -lt $sMinX) { $sMinX = $x }; if ($x -gt $sMaxX) { $sMaxX = $x }
        if ($y -lt $sMinY) { $sMinY = $y }; if ($y -gt $sMaxY) { $sMaxY = $y }
      }
    }
    $rows[$y] = $cnt
  }

  $lw = $sMaxX - $sMinX + 1; $lh = $sMaxY - $sMinY + 1
  $topRow = if ($rows.ContainsKey($sMinY)) { $rows[$sMinY] } else { 0 }
  $midRow = if ($rows.ContainsKey($sMinY + [int]($lh / 2))) { $rows[$sMinY + [int]($lh / 2)] } else { 0 }
  # fill colour sampled at the centre of the label
  $midY = $sMinY + [int]($lh / 2)
  $midX = $sMinX + [int]($lw / 2)
  $fill = $bmp.GetPixel($midX, $midY)
  $fillHex = ("#{0:X2}{1:X2}{2:X2}" -f $fill.R, $fill.G, $fill.B)
  # white text pixels inside the isolated label box
  $txt = 0
  for ($y = $sMinY; $y -le $sMaxY; $y++) {
    for ($x = $sMinX; $x -le $sMaxX; $x++) {
      $c = $bmp.GetPixel($x, $y)
      if ($c.R -gt 235 -and $c.G -gt 235 -and $c.B -gt 235) { $txt++ }
    }
  }

  Write-Output ("{0} | orbBox=({1},{2})-({3},{4}) {5}x{6} cx={7} | labelBox=({8},{9})-({10},{11}) {12}x{13} | topRowPx={14} midRowPx={15} | fill={16} | whiteTextPx={17} | airGap={18}" -f `
      $f.Name, $orbMinX, $orbMinY, $orbMaxX, $orbMaxY, ($orbMaxX - $orbMinX + 1), ($orbMaxY - $orbMinY + 1), $orbCX, `
      $sMinX, $sMinY, $sMaxX, $sMaxY, $lw, $lh, $topRow, $midRow, $fillHex, $txt, ($sMaxY - $orbMinY))
  $bmp.Dispose()
}
