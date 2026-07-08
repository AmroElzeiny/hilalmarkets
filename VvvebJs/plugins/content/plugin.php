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
Name: Content
Slug: content
Category: content
Url: https://www.vvveb.com
Description: Add text at the beginning and end of a post
Author: givanz
Version: 0.1
Thumb: content.svg
Author url: https://www.vvveb.com
Settings: /admin/index.php?module=plugins/content/settings
*/

use Vvveb\System\Event as Event;

if (! defined('V_VERSION')) {
	die('Invalid request!');
}

class ContentPlugin {
	function admin() {
		//add admin menu item
	}

	function app() {
		Event::on('Vvveb\Component\Post', 'results', __CLASS__, function ($results = false) {
			if ($results) {
				$options = Vvveb\getSetting('content-plugin', ['start', 'end']);

				$results['content']  = ($options['start'] ?? '') . $results['content'];
				$results['content'] .= $options['end'] ?? '';
			}

			return [$results];
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

$contentPlugin = new ContentPlugin();
