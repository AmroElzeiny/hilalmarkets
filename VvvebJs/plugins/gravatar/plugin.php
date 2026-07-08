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
Name: Gravatar
Slug: gravatar
Category: comments
Url: https://www.gravatar.com
Description: Show gravatar images on comments for users that don't have avatars set.
Author: givanz
Version: 0.1
Thumb: gravatar.svg
Author url: https://www.vvveb.com
Settings: /admin/index.php?module=plugins/gravatar/settings
*/

use Vvveb\System\Event;

if (! defined('V_VERSION')) {
	die('Invalid request!');
}

class GravatarPlugin {
	/**
	 * @param mixed $email
	 * @param string $s Size in pixels, defaults to 80px [ 1 - 2048 ]
	 * @param string $d Default imageset to use [ 404 | mm | identicon | monsterid | wavatar | retro]
	 * @param string $r Maximum rating (inclusive) [ g | pg | r | x ]
	 * @param mixed $img
	 */
	private function getGravatar($email, $s = 80, $d = 'mm', $r = 'g', $img = false) {
		$url = 'https://www.gravatar.com/avatar/';
		$url .= md5(strtolower(trim($email)));
		$url .= "?s=$s&d=$d&r=$r";

		return $url;
	}

	function app() {
		$types = ['comment', 'product_review', 'product_question'];

		$addGravatar = function ($comments) use ($types) {
			$options = \Vvveb\getSetting('gravatar', ['size', 'rating', 'default']);

			$commentType = false;

			foreach ($types as $type) {
				if (isset($comments[$type])) {
					$commentType = $type;

					break;
				}
			}

			if ($commentType) {
				foreach ($comments[$commentType] as &$comment) {
					if (! isset($comment['avatar']) || ! $comment['avatar']) {
						$comment['avatar_url'] = $this->getGravatar(
							$comment['email'],
							$options['size'] ?? 60,
							$options['default'] ?? 'mm',
							$options['rating'] ?? 'g'
						);
					}
				}
			}

			return [$comments];
		};

		Event::on('Vvveb\Component\Comments',  'results', __METHOD__ , $addGravatar);
		Event::on('Vvveb\Component\Reviews',   'results', __METHOD__ , $addGravatar);
		Event::on('Vvveb\Component\Questions', 'results', __METHOD__ , $addGravatar);
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

$gravatarPlugin = new GravatarPlugin();
