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
Name: Svg upload
Slug: svg-upload
Category: shipping
Url: https://www.vvveb.com
Description: Allow uploading svg images in media library
Author: givanz
Version: 0.1
Thumb: shipping.svg
Author url: https://www.vvveb.com
*/

use Vvveb\System\Event;

if (! defined('V_VERSION')) {
	die('Invalid request!');
}

class SvgUpload {
	function upload($files, $controller) {
		$controller->uploadDenyMime = array_diff($controller->uploadDenyMime, ['image/svg', 'image/svg+xml']);
		$controller->uploadDenyExtensions = array_diff($controller->uploadDenyMime, ['svg']);

		return [$files, $controller];
	}

	function admin() {
		Event::on('Vvveb\Controller\Media\Media', 'upload', __CLASS__, [$this, 'upload']);
	}

	function app() {
	}

	function __construct() {
		if (Vvveb\isEditor()) {
			return;
		}

		if (APP == 'admin') {
			$this->admin();
		} else {
			if (APP == 'app') {
				$this->app();
			}
		}
	}
}

$svgUpload = new SvgUpload();
