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
Cli example:
php cli.php admin module=plugins/markdown-import/settings action=import site_id=5 settings[path]=/docs/user
*/

namespace Vvveb\Plugins\MarkdownImport\Controller;

use League\CommonMark\Environment\Environment;
use League\CommonMark\Extension\Autolink\AutolinkExtension;
use League\CommonMark\Extension\CommonMark\CommonMarkCoreExtension;
use League\CommonMark\Extension\Strikethrough\StrikethroughExtension;
//use Vvveb\Plugins\MarkdownImport\System\Parsedown as Parsedown;
//use League\CommonMark\CommonMarkConverter;
use League\CommonMark\Extension\Table\TableExtension;
use League\CommonMark\Extension\TaskList\TaskListExtension;
//use League\CommonMark\Extension\GithubFlavoredMarkdownExtension;
use League\CommonMark\MarkdownConverter;
//use League\CommonMark\Extension\DisallowedRawHtml\DisallowedRawHtmlExtension;
use function Vvveb\__;
use Vvveb\Controller\Base;
use function Vvveb\htmlToText;
use function Vvveb\model;
use function Vvveb\slugify;
use Vvveb\Sql\categorySQL;
use Vvveb\Sql\postSQL;
use Vvveb\System\Extensions\Extensions;
use function Vvveb\truncateWords;

class Settings extends Base {
	private $cats = [];

	protected $type = 'post'; //can be also product

	protected $post_type = 'post'; //can be also product

	function removeEmoji($string) {
		// Match Enclosed Alphanumeric Supplement
		$regex_alphanumeric = '/[\x{1F100}-\x{1F1FF}]/u';
		$clear_string = preg_replace($regex_alphanumeric, '', $string);

		// Match Miscellaneous Symbols and Pictographs
		$regex_symbols = '/[\x{1F300}-\x{1F5FF}]/u';
		$clear_string = preg_replace($regex_symbols, '', $clear_string);

		// Match Emoticons
		$regex_emoticons = '/[\x{1F600}-\x{1F64F}]/u';
		$clear_string = preg_replace($regex_emoticons, '', $clear_string);

		// Match Transport And Map Symbols
		$regex_transport = '/[\x{1F680}-\x{1F6FF}]/u';
		$clear_string = preg_replace($regex_transport, '', $clear_string);

		// Match Supplemental Symbols and Pictographs
		$regex_supplemental = '/[\x{1F900}-\x{1F9FF}]/u';
		$clear_string = preg_replace($regex_supplemental, '', $clear_string);

		// Match Miscellaneous Symbols
		$regex_misc = '/[\x{2600}-\x{26FF}]/u';
		$clear_string = preg_replace($regex_misc, '', $clear_string);

		// Match Dingbats
		$regex_dingbats = '/[\x{2700}-\x{27BF}]/u';
		$clear_string = preg_replace($regex_dingbats, '', $clear_string);

		return $clear_string;
	}

	private function markdownHtml($markdown, $dir) {
		//escape html tags
		//$markdown = str_replace(['<', '>'], ['&lt;', '&gt;'], $markdown);
		//escape code blocks
		/*
		$markdown = preg_replace_callback('/```.*?```/ms', function ($matches) {
			return str_replace(['&lt;', '&gt;'], ['<', '>'],  $matches[0]);
		}, $markdown);
	

	
		$markdown = preg_replace_callback('/```.*?```/ms', function ($matches) {
			return htmlentities($matches[0]);
		}, $markdown);
		*/
		//echo $markdown;

		//$html = $text =  $this->parsedown->text($markdown);




		$markdown  = $this->removeEmoji($markdown);
		//remove numbering in headings
		$markdown  = preg_replace('/(\# \*\*)\s*\d+\\\?\.(\s*)/','$1',$markdown);
		$markdown  = preg_replace('/([#*])\s*\d+\\\?\.(\s*)/','$1$2',$markdown);
		//remove extra space before heading caused by emoji removal
		$markdown  = preg_replace('/^([#*]+)\s+([\*\w])/m','$1 $2',$markdown);
		$markdown  = str_replace('—',' - ',$markdown);

		$html            = $text            =   $this->parsedown->convert($markdown);
		$html  = str_replace('<table>','<table class="table table-bordered">',$html);
		$html  = str_replace('<thead>','<thead class="table-light">',$html);

		//admonitions support
		$html = preg_replace_callback('/<p>:::(\w+)(.*?):::<\/p>/ms', function ($matches) {
			$text = $matches[1];
			$type = str_replace(['info', 'tip', 'note', 'caution', 'danger'], ['primary', 'primary', 'secondary', 'warning', 'danger'], $text);
			$icon = str_replace(['primary', 'info', 'secondary', 'warning', 'danger'], ['&#128712;', '&#128161;', '&#8505;', '	&#128710;', '&#128711;'], $type);
			$message = strip_tags($matches[2], '<span><a>');

			return '<p class="alert alert-' . $type . ' " role="alert"><span class="fs-5 align-middle">' . $icon . '</span><strong class="initialism align-middle badge bg-white text-' . $type . '">' . $text . '</strong>' . $message . '</p>';
		}, $html);

		//process images
		$mediaPath = PUBLIC_PATH . 'media/';
		@mkdir(DIR_ROOT . $mediaPath . 'docs/');

		$html = preg_replace_callback('/<img.+?src=["\'](.+?)["\'](.+?)>/',
			function ($match) use (&$mediaPath, $dir) {
				$image = $match[1];

				//if local image copy to media
				if (strpos($image, '//') === false) {
					if (file_exists($dir . '/' . $image)) {
						copy($dir . '/' . $image, DIR_ROOT . $mediaPath . 'docs/' . $image);

						return '<a href="/media/docs/' . $image . '"><img src="/media/docs/' . $image . '"' . $match[2] . '></a>';
					}

					return '<a href="/media/docs/' . $image . '"><img src="/media/docs/' . $image . '"' . $match[2] . '></a>';
					return '<img src="/media/docs/' . $image . '"';
				}

				return $match[0];
			},$html);

		return $html;
	}

	private function traverseDir($dir) {
		if (! ($dp = opendir($dir))) {
			die("Cannot open $dir.");
		}

		while ((false !== $file = readdir($dp))) {
			if (is_dir($dir . DS . $file)) {
				if ($file != '.' && $file != '..') {
					//echo "category $dir/$file <br>";
					//check and add dir
					$this->addCategories($dir . DS . $file);
					$this->traverseDir($dir . DS . $file);
					chdir($dir);
				}
			} else {
				if ($file != '.' && $file != '..' && strrpos($file, '.md') != false) {
					//add post
					$this->add($file,$dir . DS . $file, $dir);
				}
			}
		}
		closedir($dp);

		return true;
	}

	private function addCategories($file) {
		$folder = trim(str_replace($this->docs_folder, '', $file), ' /' . DS);
		//echo '<br/>';
		$cats = explode(DS, $folder);

		$prev_category = 0;
		$category_id   = 0;

		foreach ($cats as $category_slug) {
			$cat = $this->categories->getCategoryBySlug(['slug' => $category_slug, 'taxonomy_id' => 1, 'parent_id' => $prev_category, 'site_id' => $this->global['site_id']]);

			if ($cat) {
				$prev_category = $cat['taxonomy_item_id'];
			} else {
				$cat = $this->categories->addCategory([
					'taxonomy_item' => $this->global + [
						'parent_id'   => $prev_category,
						'taxonomy_id' => 1,
					],
					'taxonomy_item_content' => $this->global + ['slug'=> $category_slug, 'name' => \Vvveb\humanReadable($category_slug), 'content' => ''],
				] + $this->global);

				$category_id = $cat['taxonomy_item'];
			}
		}

		if (! $category_id) {
			$category_id = $prev_category;
		}

		if (! isset($this->cats[$folder])) {
			$this->cats[$folder] = $category_id;
		}
	}

	private function add($filename, $file, $dir) {
		$category = trim(str_replace($this->docs_folder, '', dirname($file)), ' /' . DS);
		$slug     = \Vvveb\filter('/([^.]+)/', basename($file));
		$name     = \Vvveb\humanReadable($slug);

		$slug = \Vvveb\filter('/([^.]+)/', basename($file));
		$name = \Vvveb\humanReadable($slug);

		$markdown = file_get_contents($file);
		if (! $markdown) {
			return;
		}
		$markdown = trim($markdown);

		//get parameters if available and remove
		$params   = [];
		if (strncmp($markdown, '---', 3) === 0) {
			$markdown = preg_replace_callback('@^\s*---.+?---\s*@ms', function ($matches) use (&$params) {
				$params = Extensions::getParams($matches[0]);
				if ($params) {
					//block has parameters
					return '';
				} else {
					$matches[0];
				}
			}, $markdown, 1);
		}

		//convert markdown to html
		$html     = $this->markdownHtml($markdown, $dir);

		//get name from heading 1 if available
		$html = preg_replace_callback('/<h1>(.+?)<\/h1>/',
			function ($match) use (&$name) {
				$name = html_entity_decode(urldecode(strip_tags($match[1])));

				return ''; //remove heading 1 from content as it will be set as post name
			},$html, 1);

		if (! $name) {
			$name = $slug;
		}

		if (! isset($params['slug'])) {
			$slug = slugify($name);
			$add = "---
slug: $slug
---\n\n";


			file_put_contents($file, $add . $markdown);
		}


		$category_id = $this->cats[$category] ?? false;
		$slug        = $params['slug'] ?? $slug;
		$post_data   = $this->post->get(['slug' => $slug] + $this->global);
		$excerpt     = truncateWords(htmlToText($html), 255) ?? '';

		if (defined('CLI')) {
			echo "Importing $slug - $file";
		}

		$language_id = $this->global['language_id'];

		if (! $post_data) {
			echo ' - add';
			$data =
				[
					'post'         => $this->global + $params,
					'post_content' => [
						$language_id => $params + ['language_id' => $language_id, 'name' => $name, 'slug' => $slug, 'content' => $html, 'excerpt' => $excerpt],
					],
					'site_id' => [$this->global['site_id']],
				] + $this->global;

			$post_data = $this->post->add($data);

			if ($category_id) {
				$taxonomy_item = ['post_id' => $post_data['post'], 'taxonomy_item' => ['taxonomy_item_id' => $category_id]];
				$this->post->setPostTaxonomy($taxonomy_item);
			}
		} else {
			echo ' - edit';
			$data = [
				'post'         => $this->global + $params,
				'post_content' => [
					$language_id => $params + ['language_id' => $language_id, 'name' => $name, 'slug' => $slug, 'content' => $html, 'excerpt' => $excerpt],
				],
				'post_id' => $post_data['post_id'],
				'site_id' => [$this->global['site_id']],
			] + $this->global;

			$result = $this->post->edit($data);

			if ($category_id) {
				$taxonomy_item = ['post_id' => $post_data['post_id'], 'taxonomy_item' => ['taxonomy_item_id' => $category_id]];
				$this->post->setPostTaxonomy($taxonomy_item);
			}
		}

		echo "\n";
	}

	function import() {
		$path = $this->request->post['settings']['path'];

		if (isset($this->request->post['site_id'])) {
			$this->global['site_id'] = $this->request->post['site_id'];
		}

		if (isset($this->request->get['site_id'])) {
			$this->global['site_id'] = $this->request->get['site_id'];
		}

		if (isset($this->request->get['type'])) {
			$this->type = $this->request->get['type'];
		}

		if (isset($this->request->get['post_type'])) {
			$this->global['type'] = $this->post_type = $this->request->get['post_type'];
		}

		if (isset($this->request->get['template'])) {
			$this->global['template'] = $this->template = $this->request->get['template'];
		}

		if ($path) {
			$this->docs_folder = DIR_ROOT . $path . DS;
			$this->categories  = new categorySQL();
			$this->post        = model($this->type); //new postSQL();
			/*
					include __DIR__ . '/../../system/parsedown.php';
					$this->parsedown = new \Parsedown();
			*/
			//$this->parsedown = new Parsedown();

			require_once __DIR__ . '/../../vendor/autoload.php';

			// Define your configuration, if needed
			$config = ['html_input' => 'allow',
				'allow_unsafe_links' => true,
			];

			// Configure the Environment with all the CommonMark and GFM parsers/renderers
			$environment = new Environment($config);
			$environment->addExtension(new CommonMarkCoreExtension());
			//$environment->addExtension(new GithubFlavoredMarkdownExtension());
			$environment->addExtension(new AutolinkExtension());
			///$environment->addExtension(new DisallowedRawHtmlExtension());
			$environment->addExtension(new StrikethroughExtension());
			$environment->addExtension(new TableExtension());
			$environment->addExtension(new TaskListExtension());

			$this->parsedown = new MarkdownConverter($environment);
			/*
			$this->parsedown = new CommonMarkConverter([
				'html_input' => 'escape',
				'allow_unsafe_links' => true,
			]);
			*/

			if ($this->traverseDir($this->docs_folder)) {
				$this->view->success[] = __('Import complete!');
			}
		}

		return $this->index();
	}

	function index() {
		//$cat = $categories->getCategoryBySlug(['slug' => 	'desktop', 'parent_id' => 1]);
		//$this->traverseDir($this->docs_folder);

		return null;
	}
}
