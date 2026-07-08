<?php

/**
 * Vvveb
 *
 * Copyright (C) 2022  Ziadin Givan
 *
 * This program is free software: you can redistribute it and/or modify
 * it under the terms of the GNU Affero General Public License as
 * published by the Free Software Foundation, either version 3 of the
 * License, or (at your option) any later version.
 *
 * This program is distributed in the hope that it will be useful,
 * but WITHOUT ANY WARRANTY; without even the implied warranty of
 * MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
 * GNU Affero General Public License for more details.
 *
 * You should have received a copy of the GNU Affero General Public License
 * along with this program.  If not, see <https://www.gnu.org/licenses/>.
 *
 */

/*
Name: Minify css and js
Slug: minify
Category: performance
Url: https://www.vvveb.com
Description: Minify javascript and css for better frontend performance.
Author: givanz
Version: 0.1
Thumb: minify.svg
Author url: https://www.vvveb.com
*/

use function Vvveb\__;
use function Vvveb\isEditor;
use function Vvveb\sanitizeFileName;
use function Vvveb\slugify;
use Vvveb\System\Event;

if (! defined('V_VERSION')) {
	die('Invalid request!');
}

define('MINIFY_JS', true);
define('MINIFY_CSS', true);

class MinifyPlugin {
	//load minifier files only when minifying to avoid bloat loading them on every page request
	function initMinifier() {
		if (MINIFY_JS || MINIFY_CSS) {
			$path = __DIR__ . DS . 'system' . DS;

			require_once $path . '/minify/Minify.php';

			require_once $path . '/minify/CSS.php';

			require_once $path . '/minify/JS.php';

			require_once $path . '/minify/Exception.php';

			require_once $path . '/minify/Exceptions/BasicException.php';

			require_once $path . '/minify/Exceptions/FileImportException.php';

			require_once $path . '/minify/Exceptions/IOException.php';

			require_once $path . '/minify/ConverterInterface.php';

			require_once $path . '/minify/Converter.php';
		}
	}

	function admin() {
		//add admin menu item
		$admin_path = \Vvveb\adminPath();
		Event::on('Vvveb\Controller\Base', 'init-menu', __CLASS__, function ($menu) use ($admin_path) {
			$menu['plugins']['items']['minify-plugin'] = [
				'name'     => __('Minify'),
				'url'      => '/admin/',
				'icon-img' => PUBLIC_PATH . 'plugins/minify/minify.svg',
			];

			return [$menu];
		});
	}

	function minifyJs($js, $path = '') {
		$minifier = new MatthiasMullie\Minify\JS();
		$minifier->add($js);

		return $minifier->minify();
	}

	function minifyCss($css, $path = '') {
		$minifier = new MatthiasMullie\Minify\CSS();
		$minifier->add($css);

		return $minifier->minify();
	}

	function processJs(&$vTpl, $template, $theme, $prefixSelector = 'head > ') {
		$inlineJs = '';
		/*
		$scripts = $vTpl->query($prefixSelector . 'script');
		foreach ($scripts as $style) {
			$inlineCss .= trim($style->textContent);
		}
		*/

		$scripts = $vTpl->query($prefixSelector . 'script');
		$length  = $scripts->length;

		if ($length > 1) {
			$fileName         = $theme . '-';
			$content          = '';
			$names            = [];
			$customCss        = '';
			$moveInlineBottom = true;

			$document = $vTpl->getDocument();

			if (MINIFY_JS) {
				$minifier = new MatthiasMullie\Minify\JS();
			}

			$i             = 0;
			$lastScript    = null;
			$removeScripts = [];

			foreach ($scripts as $link) {
				$href = $link->getAttribute('src');
				//skip external js
				if (strncmp($href, 'http', 4) === 0) {
					$length--;

					continue;
				}

				//skip no minify
				if ($link->hasAttribute('data-no-minify')) {
					$length--;

					continue;
				}

				//skip inline
				if (! $link->hasAttribute('src')) {
					if ($moveInlineBottom) {
						$body = $document->getElementsByTagName('body')->item(0);
						$body->appendChild($link);
					}
					$length--;

					continue;
				}

				//skip module
				if ($link->hasAttribute('type') &&
					(($type = $link->getAttribute('type') == 'module') || ($type = 'application/ld+json') || ($type = 'speculationrules'))
				) {
					$length--;

					continue;
				}

				$i++;

				$path = DIR_THEMES . $theme . DS;
				$href = str_replace(PUBLIC_PATH . $theme . '/', '', $href);

				//absolute path or upper level then use public folder
				if ($href && ($href[0] == '/' || strncmp($href, '../../', 6) === 0)) {
					$path = DIR_PUBLIC;
				}

				$file = $path . sanitizeFileName($href);

				if (! file_exists($file)) {
					$i--;

					continue;
				}

				if (MINIFY_JS) {
					//$styles = $this->minifyJs($styles, $href);
					$minifier->add($file);
				} else {
					//$content .= "\n/* $href */\n\n" . $styles;
					$styles = file_get_contents($file);
					$content .= "\n" . $styles;
				}

				$names[] = slugify(basename(str_replace('.min', '', $href), '.js'));
				$removeScripts[] = $link;
				$lastScript = $link;
			}

			if ($lastScript) {
				sort($names);
				$fileName .= implode('-', $names) . '.js';

				if ($inlineJs) {
					//$content .= "\n/* -- inline - */\n\n" . $inlineJs;
					$content .= "\n" . $inlineJs;
				}

				//if (file_put_contents(DIR_PUBLIC . DS . 'assets-cache' . DS . $fileName, $content)) {
				if ($lastScript && $lastScript->parentNode && $minifier->minify(DIR_PUBLIC . DS . 'assets-cache' . DS . $fileName)) {
					$minified = $document->createElement('script');
					$minified->setAttribute('src', '/assets-cache/' . $fileName);
					$lastScript->parentNode->replaceChild($minified, $lastScript);
					//if minified succeeded remove all scripts
					foreach ($removeScripts as $script) {
						if (! $script->isSameNode($lastScript)) {
							//$script->remove();
							$script->parentNode->removeChild($script);
						}
					}
					//remove inline js
					/*
					$inlineCss = $vTpl->query($prefixSelector . 'script');
					foreach ($inlineCss as $style) {
						$style->remove();
					}*/
				}
			}
		}
	}

	function processCss(&$vTpl, $template, $theme, $prefixSelector = 'head > ') {
		$css       = $vTpl->query($prefixSelector . 'style');
		$inlineCss = '';

		foreach ($css as $style) {
			$inlineCss .= trim($style->textContent);
		}

		$css    = $vTpl->query($prefixSelector . 'link[rel="stylesheet"]');
		$length = $css->length;

		if ($length > 1) {
			$fileName  = $theme . '-';
			$content   = '';
			$names     = [];
			$customCss = '';

			$document = $vTpl->getDocument();

			if (MINIFY_CSS) {
				$minifier = new MatthiasMullie\Minify\CSS();
			}

			$i         = 0;
			$lastStyle = null;
			$remove    = [];

			foreach ($css as $link) {
				$href = $link->getAttribute('href');
				//skip external css
				if (! $href || (strncmp($href, 'http', 4) === 0)) {
					$length--;

					continue;
				}

				//skip no minify
				if ($link->hasAttribute('data-no-minify')) {
					$length--;

					continue;
				}

				$i++;

				$path = DIR_THEMES . $theme . DS;
				$href = str_replace(PUBLIC_PATH . $theme . '/', '', $href);

				//absolute path or upper level then use public folder
				if ($href && ($href[0] == '/' || strncmp($href, '../../', 6) === 0)) {
					$path = DIR_PUBLIC;
				}

				$file = $path . sanitizeFileName($href);

				if (! file_exists($file)) {
					$i--;

					continue;
				}

				if (MINIFY_CSS) {
					//$styles = $this->minifyJs($styles, $href);
					$minifier->add($file);
				} else {
					//$content .= "\n/* $href */\n\n" . $styles;
					$styles = file_get_contents($file);
				}

				//add custom css last for highest priority
				if (substr_compare($href, 'custom.css', -10, 10) === 0) {
					if (file_exists($file)) {
						$customCss = file_get_contents($file);
					}
				} else {
					//$content .= "\n/* $href */\n\n" . $styles;
					if (! MINIFY_CSS) {
						$content .= "\n" . $styles;
					}
				}

				$names[]   = slugify(basename(str_replace('.min', '', $href), '.css'));
				$remove[]  = $link;
				$lastStyle = $link;
			}

			if ($lastStyle/* && ($i >= $length)*/) {
				sort($names);
				$fileName .= implode('-', $names) . '.css';

				if ($inlineCss) {
					if (MINIFY_CSS) {
						$minifier->add($inlineCss);
					} else {
						//$content .= "\n/* -- inline - */\n\n" . $inlineCss;
						$content .= "\n" . $inlineCss;
					}
				}

				if ($customCss) {
					if (MINIFY_CSS) {
						$minifier->add($inlineCss);
					} else {
						//$content .= "\n/* -- custom.css - */\n\n" . $customCss;
						$content .= "\n" . $customCss;
					}
				}

				//if (file_put_contents(DIR_PUBLIC . DS . 'assets-cache' . DS . $fileName, $content)) {
				if ($lastStyle && $lastStyle->parentNode && $minifier->minify(DIR_PUBLIC . DS . 'assets-cache' . DS . $fileName)) {
					$minified = $document->createElement('link');
					$minified->setAttribute('rel','stylesheet');
					$minified->setAttribute('href', '/assets-cache/' . $fileName);

					foreach ($remove as $style) {
						if (! $style->isSameNode($lastStyle)) {
							$style->parentNode->removeChild($style);
						}
					}

					$lastStyle->parentNode->replaceChild($minified, $lastStyle);
					//remove inline css
					$inlineCss = $vTpl->query($prefixSelector . 'style');

					foreach ($inlineCss as $style) {
						//$style->remove();
						$style->parentNode->removeChild($style);
					}
				}
			}
		}
	}

	function app() {
		//don't minify if page is opened in editor
		if (isEditor()) {
			return;
		}
		Event::on('Vvveb\System\Core\View', 'compile:after', __CLASS__, function ($template, $htmlFile, $tplFile, $vTpl, $view) {
			$theme = $view->getTheme();

			$this->initMinifier();

			$this->processCss($vTpl, $template, $theme);
			$this->processCss($vTpl, $template, $theme, 'body > ');

			$this->processJs($vTpl, $template, $theme);
			$this->processJs($vTpl, $template, $theme, 'body > ');

			return [$template, $htmlFile, $tplFile, $vTpl, $view];
		});
	}

	function __construct() {
		if (APP == 'admin') {
			$this->admin();
		} else {
			if (APP == 'app') {
				$this->app();
			}
		}
	}
}

$minifyPlugin = new MinifyPlugin();
