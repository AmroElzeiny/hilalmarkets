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

/**
 * @package SEO Plugin
 * @version 0.1
 */
/*
Name: SEO Optimization
Slug: seo
Url: https://www.vvveb.com
Description: Add SEO capabilities like meta for content and schema markup.
Author: givanz
Version: 0.1
Thumb: seo.svg
Author url: https://www.vvveb.com
Settings: /admin/index.php?module=plugins/seo/settings
*/

use Vvveb\Plugins\Seo\Admin;
use Vvveb\Plugins\Seo\App;
use Vvveb\Plugins\Seo\Graphql;
use Vvveb\Plugins\Seo\Rest;

if (! defined('V_VERSION')) {
	die('Invalid request!');
}

class SeoPlugin {
	function admin() {
		$admin = new Admin();
	}

	function app() {
		$app = new App();
	}

	function rest() {
		$rest = new Rest();
	}

	function graphql() {
		$graphql = new Graphql();
	}

	function __construct() {
		$app = APP;
		//$this->$app();
		switch (APP) {
			case 'admin':
				$this->admin();

				break;

			case 'app':
				$this->app();

				break;

			case 'rest':
				$this->rest();

				break;

			case 'graphql':
				$this->graphql();

				break;
		}
	}
}

$seoPlugin = new SeoPlugin();
