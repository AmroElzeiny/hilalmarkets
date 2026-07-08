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

namespace Vvveb\Controller\Posts;

use Vvveb\Controller\Listing;

#[\AllowDynamicProperties]
class Index extends Listing {
	public static $defaultOptions = [
		'page'               => ['url', 1],
		'post_id'            => 'url',
		'language_id'        => null,
		'source'             => 'autocomplete',
		'type'               => 'post',
		'site_id'            => null,
		'start'              => null,
		'limit'              => ['url', 8],
		'order_by'           => 'post_id',
		'direction'          => 'desc',
		'status'             => 'publish',
		'excerpt_limit'      => 200,
		'comment_count'      => 1,
		'comment_status'     => 1,
		'taxonomy_item_id'   => NULL,
		'taxonomy_item_slug' => NULL,
		'search'             => NULL,
		'like'               => NULL,
		'admin_id'           => NULL,
		//archive
		'month'              => NULL,
		'year'               => NULL,
		'image_size'         => 'medium',
		'categories'         => null,
		'tags'               => null,
		'taxonomy'           => null,
		'username'           => null,
	];

	protected $type = 'post';

}
