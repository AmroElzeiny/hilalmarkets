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
Name: Table of content for posts
Slug: toc-posts
Category: content
Url: https://www.vvveb.com
Description: Show navigable table of content for posts
Author: givanz
Version: 0.1
Thumb: toc-posts.svg
Author url: https://www.vvveb.com
*/

use \Vvveb\System\Event as Event;
use Vvveb\System\Core\View;

if (! defined('V_VERSION')) {
	die('Invalid request!');
}

class TocPostsPlugin {
	private $headings;

	private $html;

	public function setHtml($html) {
		$this->html = $html;
		$this->setHeadings();
	}

	public function html() {
		$html    = $this->html;
		$matches = $this->headings;

		foreach ($matches[1] as $index => $item) {
			$html = str_replace(
				'>' . $item . '</h',
				' id="' . $matches[2][$index] . '">' . $item . '</h',
				$html
			);
		}

		return $html;
	}

	public function getHtml() {
		$this->setHeadingIds();

		return $this->html;
	}

	public function setHeadingIds() {
		foreach ($this->headings[0] as $index => $heading) {
			$slug       = $this->headings[2][$index];
			$heading_id = preg_replace('/<h(\d+)/',"<h$1 id=\"$slug\"", $heading);
			$this->html = str_replace($heading, $heading_id, $this->html);
		}
	}

	public function list() {
		$out         = '';
		$matches     = $this->headings;
		$old_depth   =  substr($matches[0][0] ?? '', 2, 1) ?? 0;
		$current_url = $_SERVER['REQUEST_URI'];
		$list        = [];

		foreach ($matches[1] as $key => $item) {
			$depth   = (int)substr($matches[0][$key], 2, 1);
			/*
			$url     = $current_url . '#' . $matches[2][$key];
			$onclick = "document.location.hash='{$matches[2][$key]}';return false";
			*/
			$url     ='#' . $matches[2][$key];
			$onclick = '';

			$list[]    = ['name' => strip_tags($item), /* 'slug' => $slug,*/ 'url' => $url, 'onclick' => $onclick, 'depth' => $depth - 1];
			$old_depth = $depth;
		}

		return $list;
	}

	public function listHtml() {
		$out         = '';
		$matches     = $this->headings;
		$old_depth   =  substr($matches[0][0], 2, 1);
		$current_url = $_SERVER['REQUEST_URI'];

		foreach ($matches[1] as $key => $item) {
			$depth = substr($matches[0][$key], 2, 1);

			if ($old_depth > $depth) {
				$out .= "</ol>\n";
			} elseif ($old_depth < $depth) {
				$out .= "<li>\n";
				$out .= "<ol>\n";
			}

			$url = $current_url . '#' . $matches[2][$key];

			$out .= sprintf("
				<li>
				  <a href='%s'>%s</a>
				</li>",
				$url, $item);
			$old_depth = $depth;
		}

		return "<ol>\n$out\n</ol>\n\n";
	}

	private function setHeadings() {
		preg_match_all('|<h[^>]+>(.*)</h[^>]+>|iU', $this->html, $matches);

		$slugs = [];

		foreach ($matches[1] as &$item) {
			$item = htmlspecialchars_decode($item);
			$slugs[] = $this->slugify(strip_tags(preg_replace('/\d+/', '', $item)));
		}

		$this->headings    = $matches;
		$this->headings[2] = $slugs;
	}

	function slugify($string, $replace = [], $delimiter = '-') {
		if (! empty($replace)) {
			$string = str_replace((array) $replace, ' ', $string);
		}
		$string = preg_replace('/[^a-zA-Z0-9\/_|+ -]/', '', $string);
		$string = strtolower($string);
		$string = preg_replace('/[\/_|+ -]+/', $delimiter, $string);
		$string = trim($string, $delimiter);

		return $string;
	}

	function admin() {
		//add admin menu item
	}

	function generateToc($post) {
	}

	function toc(&$post) {
		$this->setHtml($post['content']);
		$this->getHtml();

		$post['content'] = $this->getHtml();
		$post['toc']     = $this->list();

		return $post;
	}

	function app() {
		//register component

		Event::on('Vvveb\Component\Post', 'results', __CLASS__, function ($results = false) {
			if ($results && isset($results['content'])) {
				$this->toc($results);
			}

			return [$results];
		});

		Event::on('Vvveb\Component\Posts', 'results', __CLASS__, function ($results = false) {
			if ($results && isset($results[0])) {
				$this->toc($results[0]);
			}

			return [$results];
		});

		$view       = View::getInstance();
		$template   = $view->getTemplateEngineInstance();
		$template->loadTemplateFile(__DIR__ . '/app/template/toc-posts.tpl');
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

$tocPostsPlugin = new TocPostsPlugin();
