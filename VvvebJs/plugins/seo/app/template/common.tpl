//copy default meta tags if theme is lacking
head|append = from(/plugins/seo/head.html|head > *)

/* Open Graph protocol */

/*
meta[property="og:title]|content = $title

meta[property="og:locale"]|content = $this->seo['locale']
meta[property="og:type"]|content = $this->seo['type']
meta[property="og:title"]|content = $this->seo['title']
meta[property="og:description"]|content = $this->seo['description']
meta[property="og:url"]|content = $this->seo['url']
meta[property="og:site_name"]|content = $this->seo['site_name']

meta[property="article:author"]|content = $this->seo['author']
meta[property="article:published_time"]|content = $this->seo['published_time']
meta[property="article:modified_time"]|content = $this->seo['modified_time']


meta[property="og:image"]|content = $this->seo['image']
meta[property="og:image:width"]|content = $this->seo['image:width']
meta[property="og:image:height"]|content = $this->seo['image:height']
meta[property="og:image:type"]|content = $this->seo['image:type']


meta[name="twitter:card"]|content = $this->seo['twitter']['card']
meta[name="twitter:creator"]|content = $this->seo['twitter']['creator']
meta[name="twitter:site"]|content = $this->seo['twitter']['site']
meta[name="twitter:label1"]|content = $this->seo['twitter']['label1']
meta[name="twitter:data1"]|content = $this->seo['twitter']['data1']
meta[name="twitter:label2"]|content = $this->seo['twitter']['label2']
meta[name="twitter:data2"]|content = $this->seo['twitter']['data2']
*/


meta[name="author"]|content = $this->seo['author']

import(/plugins/seo/app/template/meta-ns.tpl, {"attribute":"property","meta":"og"})
import(/plugins/seo/app/template/meta-ns.tpl, {"attribute":"property","meta":"article"})
import(/plugins/seo/app/template/meta-ns.tpl, {"attribute":"name","meta":"twitter"})

//remove holder attribute to avoid vtpl to restore script content
script[type="application/ld+json"]|removeAttribute = 'holder'



head > meta[itemprop]|deleteAllButFirst

head > meta[itemprop]|before = <?php 
if (isset($this->seo['meta']['itemprop'])) {
	foreach ($this->seo['meta']['itemprop'] as $name => $value) {
?>		

head > meta[itemprop]|content  = $value

head > meta[itemprop]|after = <?php 
	} 
}	
?>

head > meta[itemprop]|deleteAllButFirst

head > meta[itemprop]|before = <?php 
if (isset($this->seo['meta']['itemprop'])) {
	foreach ($this->seo['meta']['itemprop'] as $name => $value) {
?>		

head > meta[itemprop]|content  = $value

head > meta[itemprop]|after = <?php 
	} 
}	
?>

import(/plugins/seo/app/template/meta.tpl, {"attribute":"name","meta":"site-verification"})


@schema = script[type="application/ld+json"]

@schema|deleteAllButFirst

@schema|before = <?php 
if (isset($this->seo['schema'])) {
	foreach ($this->seo['schema'] as $file =>  $schema) {
?>		

@schema = <?php echo $schema;?>

@schema|after = <?php 
	} 
}	
?>
