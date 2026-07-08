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
Name: DiceBear
Slug: dicebear
Category: comments
Url: https://www.dicebear.com
Description: Show dicebear images on comments for users that don't have avatars set.
Author: givanz
Version: 0.1
Thumb: dicebear.svg
Author url: https://www.vvveb.com
Settings: /admin/index.php?module=plugins/dicebear/settings
*/

use Vvveb\System\Event;

if (! defined('V_VERSION')) {
	die('Invalid request!');
}

define('DICEBAR_URL', 'https://api.dicebear.com/7.x/');

class DiceBearPlugin {
	protected function getDiceBear($email, $url = DICEBAR_URL, $width = 60, $styleName = 'fun-emoji', $flip = 'false') {
		$url  = "$url$styleName/svg";
		$host = $_SERVER['HTTP_HOST'] ?? '';
		$seed = md5(strtolower(trim($host . $email)));
		$url .= "?seed=$seed";

		if ($flip == 'true') {
			$url .= '&flip=true';
		}

		return $url;
	}

	function app() {
		$types = ['comment', 'product_review', 'product_question'];

		$addDiceBear = function ($comments) use ($types) {
			$options = \Vvveb\getSetting('dicebear', ['url', 'size', 'style', 'flip']);

			$commentType = false;

			foreach ($types as $type) {
				if (isset($comments[$type])) {
					$commentType = $type;

					break;
				}
			}

			if ($commentType) {
				foreach ($comments[$commentType] as &$comment) {
					if ((! isset($comment['avatar']) || ! $comment['avatar']) &&
						(isset($comment['email']) || ! $comment['email'])) {
						$comment['avatar_url'] = $this->getDiceBear(
							$comment['email'],
							$options['url'] ?? DICEBAR_URL,
							$options['size'] ?? 60,
							$options['style'] ?? 'fun-emoji',
							$options['flip'] ?? 'false'
						);
					}

					if (isset($options['size'])) {
						$comment['size'] = $options['size'];
					}
				}
			}

			return [$comments];
		};

		Event::on('Vvveb\Component\Comments',  'results', __METHOD__ , $addDiceBear);
		Event::on('Vvveb\Component\Reviews',   'results', __METHOD__ , $addDiceBear);
		Event::on('Vvveb\Component\Questions', 'results', __METHOD__ , $addDiceBear);
	}

	function __construct() {
		if (APP == 'admin') {
			//$this->admin();
		} else {
			if (APP == 'app') {
				$this->app();
			}
		}
	}
}

$dicebearPlugin = new DiceBearPlugin();
