meta[{{attribute}}^="{{meta}}:"]|deleteAllButFirst

meta[{{attribute}}^="{{meta}}:"]|before = <?php 
if (isset($this->seo['meta']['{{meta}}'])) {
	foreach ($this->seo['meta']['{{meta}}'] as $name => $value) {
?>		


meta[{{attribute}}^="{{meta}}:"]|after = <?php 
	} 
}	
?>

meta[{{attribute}}^="{{meta}}:"]|content  = $value
//must change attribute last to be able to use selector
meta[{{attribute}}^="{{meta}}:"]|{{attribute}} = <?php echo "{{meta}}:$name";?>
